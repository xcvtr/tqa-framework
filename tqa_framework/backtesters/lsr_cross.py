"""LSR-CROSS backtester — 1:1 порт Python оригинала.

Наследует Backtester для PG-сохранения, но run() и simulate_trade()
воспроизводят архитектуру TQA-crypto/engine/lsr_cross.py 1:1:

  - detect_events → load_lsr_data (тот же z-score, те же cross-условия)
  - simulate_trade → entry по следующему 5m бару, exit по 1m hi/lo
  - backtest → per-event portfolio loop (как Python, не per-tick)
  - pyramiding: +0.5x риск при +5% в сторону
"""

from __future__ import annotations

import json
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List

from tqa_framework.engine.backtester import Backtester
from tqa_framework.engine.exchange_base import Signal

logger = logging.getLogger(__name__)


class LsrCrossBacktester(Backtester):
    """LSR-CROSS backtester — 1:1 порт Python оригинала."""

    # Константы (фиксированы — чекпойнт 20260822)
    LEVERAGE = 1
    COMM = 0.0010
    SLIP = 0.0005
    PYR_TRIGGER = 0.05
    PYR_ADD = 0.5
    HOLD_H = 120  # hold timeout hours

    SYM_RISK = {
        'ADAUSDT': 1.3, 'ARBUSDT': 1.2, 'OPUSDT': 1.1, 'AVAXUSDT': 1.05,
        'DOGEUSDT': 1.0, 'XRPUSDT': 1.0, 'ETHUSDT': 1.0, 'SOLUSDT': 1.0,
        'BNBUSDT': 1.0, 'LINKUSDT': 1.0, 'NEARUSDT': 0.9, 'APTUSDT': 0.9
    }
    # These will be overridden in run() from params
    EXCLUDE_SYMS = {'SOLUSDT'}
    EXCLUDE_HOURS = {22, 23}
    EXCLUDE_DOW = set()  # Wednesday

    def _parse_ts(self, val) -> datetime:
        if isinstance(val, datetime):
            return val.replace(tzinfo=None) if val.tzinfo else val
        return datetime.fromisoformat(str(val).replace('Z', '+00:00')).replace(tzinfo=None)

    def _dt64_to_dt(self, val):
        """Convert numpy datetime64 to datetime (Python оригинал использует pd.Timestamp)."""
        return self._parse_ts(str(val))

    def simulate_trade(self, symbol, event_ts, direction_int,
                       klines_5m, klines_1m,
                       sl_pct=0.05, tp_pct=0.18, hold_h=120,
                       pyr_trigger=0.05, pyr_add=0.5,
                       trail_act=0.02, trail_dist=0.015, trail_lock=0.02,
                       return_exit_ts=False,
                       _cache_5m=None, _cache_1m=None):
        """1:1 порт simulate_trade() из lsr_cross.py.

        Args:
            event_ts: datetime64[ns] или datetime LSR события
            direction_int: +1=LONG, -1=SHORT
            klines_5m: 5m bars list [{ts, open, high, low, close}]
            klines_1m: 1m bars list [{ts, open, high, low, close}]
            _cache_5m: pre-built numpy array for 5m opening prices
            _cache_1m: pre-built (ts, hi, lo, cl) numpy arrays for 1m
            return_exit_ts: если True, возвращает exit_ts

        Returns:
            (pnl_eff, mtm_dd) или None если нет баров для входа
        """
        # — Вход: close следующего 5m бара после сигнала —
        if _cache_5m is not None:
            px_t5, px_c5 = _cache_5m
        else:
            px_t5 = np.array([self._parse_ts(b['ts']).timestamp() for b in klines_5m])
            px_c5 = np.array([b['close'] for b in klines_5m])
        ev_ts = self._parse_ts(event_ts).timestamp()
        ei = np.searchsorted(px_t5, ev_ts, side='right')
        if ei + 1 >= len(px_c5):
            return None
        entry = float(px_c5[ei])

        # — 1m сканирование (bounded) —
        if _cache_1m is not None:
            px_t, px_h, px_l, px_c = _cache_1m
        else:
            px_t = np.array([self._parse_ts(b['ts']).timestamp() for b in klines_1m])
            px_h = np.array([b['high'] for b in klines_1m])
            px_l = np.array([b['low'] for b in klines_1m])
            px_c = np.array([b['close'] for b in klines_1m])

        # start_idx = первый 1m бар после закрытия 5m бара входа
        entry_ts = px_t5[ei]  # timestamp 5m бара входа (open)
        entry_close_ts = entry_ts + (px_t5[1] - px_t5[0])  # open + 5m ≈ close
        si = np.searchsorted(px_t, entry_close_ts, side='right')
        if si + 1 >= len(px_c):
            return None

        # 1m баров в 5m периоде
        scan_mult = 60  # 60 1m-баров на час
        end_idx = min(si + int(hold_h * scan_mult), len(px_c) - 1)

        direction = direction_int
        sl_px = entry * (1 - sl_pct) if direction == 1 else entry * (1 + sl_pct)
        tp_px = entry * (1 + tp_pct) if direction == 1 else entry * (1 - tp_pct)
        pyr_px = entry * (1 + pyr_trigger) if direction == 1 else entry * (1 - pyr_trigger)
        exit_px = None
        exit_ret_ts = None
        mtm_worst = entry
        added = False
        base_risk = 1.0
        trail_active = False
        peak = entry

        for j in range(si, end_idx + 1):
            hi, lo = float(px_h[j]), float(px_l[j])
            if direction == 1:  # LONG
                mtm_worst = min(mtm_worst, lo)
                if not added and hi >= pyr_px:
                    added = True
                    base_risk += pyr_add
                if trail_act is not None:
                    if not trail_active and hi >= entry * (1 + trail_act):
                        trail_active = True
                        peak = hi
                        sl_px = entry
                    if trail_active:
                        if hi > peak:
                            peak = hi
                        new_sl = float(peak * (1 - trail_dist))
                        if trail_lock > 0:
                            new_sl = max(new_sl, entry * (1 + trail_lock))
                        sl_px = max(sl_px, new_sl)
                if lo <= sl_px:
                    exit_px = float(sl_px)
                    exit_ret_ts = px_t[j]
                    break
                if not trail_active and tp_pct > 0 and hi >= tp_px:
                    exit_px = float(tp_px)
                    exit_ret_ts = px_t[j]
                    break
            else:  # SHORT
                mtm_worst = max(mtm_worst, hi)
                if not added and lo <= pyr_px:
                    added = True
                    base_risk += pyr_add
                if trail_act is not None:
                    if not trail_active and lo <= entry * (1 - trail_act):
                        trail_active = True
                        peak = lo
                        sl_px = entry
                    if trail_active:
                        if lo < peak:
                            peak = lo
                        new_sl = float(peak * (1 + trail_dist))
                        if trail_lock > 0:
                            new_sl = min(new_sl, entry * (1 - trail_lock))
                        sl_px = min(sl_px, new_sl)
                if hi >= sl_px:
                    exit_px = float(sl_px)
                    exit_ret_ts = px_t[j]
                    break
                if not trail_active and tp_pct > 0 and lo <= tp_px:
                    exit_px = float(tp_px)
                    exit_ret_ts = px_t[j]
                    break

        if exit_px is None:
            exit_px = float(px_c[end_idx])
            exit_ret_ts = datetime.fromtimestamp(px_t[end_idx])
        elif exit_ret_ts is not None:
            exit_ret_ts = datetime.fromtimestamp(exit_ret_ts)

        if direction == 1:
            pnl = (exit_px - entry) / entry - self.COMM - self.SLIP
            mtm_dd_pct = max(0.0, (entry - mtm_worst) / entry)
        else:
            pnl = (entry - exit_px) / entry - self.COMM - self.SLIP
            mtm_dd_pct = max(0.0, (mtm_worst - entry) / entry)

        # DEBUG: log first 3 trades per ticker
        if not hasattr(self, '_debug_cnt'):
            self._debug_cnt = {}
        sym_cnt = self._debug_cnt.get(symbol, 0)
        if sym_cnt < 3:
            self._debug_cnt[symbol] = sym_cnt + 1
            logger.info("DEBUG simulate_trade %s: event_ts=%s ts()=%s entry_px=%.4f exit_px=%.4f pnl_eff=%.4f dir=%d exit_ts=%s si=%d end_idx=%d first_bar_ts=%s last_bar_ts=%s n_1m=%d ei=%d entry_close_ts=%s",
                        symbol, event_ts, ev_ts, entry, exit_px, pnl * base_risk, direction, exit_ret_ts, si, end_idx,
                        datetime.fromtimestamp(px_t[si]) if si < len(px_t) else "N/A",
                        datetime.fromtimestamp(px_t[min(end_idx, len(px_t)-1)]) if end_idx < len(px_t) else "N/A",
                        len(px_t), ei, datetime.fromtimestamp(entry_close_ts) if isinstance(entry_close_ts, (int, float)) else entry_close_ts)
            # Also print a few bar timestamps around si
            for dbg_i in range(max(0,si-2), min(len(px_t), si+3)):
                logger.info("DEBUG 1m_bar[%d]: ts=%s physical=%s hi=%.2f lo=%.2f",
                            dbg_i, px_t[dbg_i], datetime.fromtimestamp(px_t[dbg_i]) if dbg_i < len(px_t) else "N/A",
                            float(px_h[dbg_i]) if dbg_i < len(px_h) else 0, float(px_l[dbg_i]) if dbg_i < len(px_l) else 0)

        if return_exit_ts:
            return pnl * base_risk, mtm_dd_pct * base_risk, entry, exit_px, direction, base_risk, exit_ret_ts
        return pnl * base_risk, mtm_dd_pct * base_risk, entry, exit_px, direction, base_risk

    def run(self) -> dict:
        """Запустить бэктест LSR-CROSS (1:1 с Python оригиналом).

        Per-event portfolio loop: загружает события → сортирует по времени →
        для каждого simulate_trade → обновляет капитал → сохраняет в PG.
        """
        # ── Read params from strategy_params (with defaults) ──
        self.z_score = float(getattr(self, 'z_score', self.strategy_params.get('z_score', 2.0)))
        _exclude_hours = getattr(self, 'exclude_hours', self.strategy_params.get('exclude_hours', []))
        self.EXCLUDE_HOURS = set(int(h) for h in _exclude_hours) if _exclude_hours else set()
        _exclude_dow = getattr(self, 'exclude_dow', self.strategy_params.get('exclude_dow', []))
        self.EXCLUDE_DOW = set(int(d) for d in _exclude_dow) if _exclude_dow else set()
        _exclude_syms = getattr(self, 'exclude_syms', self.strategy_params.get('exclude_syms', []))
        self.EXCLUDE_SYMS = set(_exclude_syms) if _exclude_syms else set()
        self.exclude_enabled = bool(getattr(self, 'exclude_enabled',
                                            self.strategy_params.get('exclude_enabled', True)))

        self.pg.ensure_schemas()
        self.pg.ensure_tables_backtest()

        # Фиксируем end_time
        import requests as _req
        _sym0 = self.tickers[0]["symbol"]
        _q = f"SELECT max(timestamp) FROM crypto.klines WHERE symbol='{_sym0}' AND interval='5m' FORMAT TabSeparated"
        _r = _req.get(self.ch_host, params={"query": _q}, timeout=15)
        _r.raise_for_status()
        ch_latest = _r.text.strip()
        end_time = self.end_time_override if self.end_time_override else (ch_latest if ch_latest else "")
        logger.info("CH последний бар: %s", end_time)

        if getattr(self, 'end_time_override', None):
            end_time = self.end_time_override

        # ── 1. Загружаем бары и LSR события для всех символов ──
        logger.info("Загрузка данных для %d тикеров...", len(self.tickers))

        k5_cache: Dict[str, list] = {}
        k1_cache: Dict[str, list] = {}
        all_events: list[tuple] = []

        from tqa_framework.engine.detect import load_m1_from_ch

        for ticker_cfg in self.tickers:
            symbol = ticker_cfg["symbol"]
            if symbol in self.EXCLUDE_SYMS:
                logger.info("  %s: EXCLUDED (SOLUSDT)", symbol)
                continue

            # 5m бары
            bars_5m = load_m1_from_ch(
                symbol, self.days * 24, self.ch_host, self.ch_db,
                end_time=end_time, source=getattr(self, 'data_source', 'bars'), interval='5m'
            )
            if not bars_5m or len(bars_5m) < 200:
                logger.warning("  %s: мало 5m баров (%d), пропускаем", symbol, len(bars_5m or []))
                continue
            k5_cache[symbol] = bars_5m
            logger.info("  %s: %d 5m баров", symbol, len(bars_5m))

            # 1m бары
            bars_1m = load_m1_from_ch(
                symbol, self.days * 24, self.ch_host, self.ch_db,
                end_time=end_time, source=getattr(self, 'data_source', 'bars'), interval='1m'
            )
            if not bars_1m:
                logger.warning("  %s: нет 1m баров, используем 5m", symbol)
                bars_1m = bars_5m
            k1_cache[symbol] = bars_1m
            logger.info("  %s: %d 1m баров", symbol, len(bars_1m))

            # — LSR события (z-score cross) — загружаем с буфером 720h для z-score warmup
            lsr_signals = self.load_lsr_data(
                symbol, hours=self.days * 24 + 720, end_time=end_time,
                z_score_threshold=self.z_score,
            )
            if not lsr_signals:
                logger.info("  %s: нет LSR событий", symbol)
                continue

            # Применяем exclude часы/DOW
            for sig in lsr_signals:
                try:
                    dt = self._parse_ts(sig['ts'])
                    if self.exclude_enabled:
                        if dt.hour in self.EXCLUDE_HOURS:
                            continue
                        if dt.weekday() in self.EXCLUDE_DOW:
                            continue
                except (ValueError, AttributeError):
                    pass
                direction_int = 1 if sig['direction'] == 'LONG' else -1
                all_events.append((self._parse_ts(sig['ts']), symbol, direction_int))

            logger.info("  %s: %d событий после фильтрации", symbol, len([e for e in all_events if e[1] == symbol]))

        all_events.sort(key=lambda x: x[0])

        if not all_events:
            logger.error("Нет событий ни по одному символу")
            empty = {"summary": {"strategy": self.strategy_name, "tickers": [],
                                  "tf": self.tf_minutes, "days": self.days,
                                  "start_equity": self.initial_equity, "end_equity": self.initial_equity,
                                  "total_return": 0.0, "mdd": 0.0, "win_rate": 0.0,
                                  "profit_factor": float('inf'), "total_trades": 0, "calmar_ratio": 0.0},
                      "trades": [], "equity_curve": []}
            return empty

        # ── 2. Pre-compute all trades with exit timestamps ──
        from collections import OrderedDict

        # Build numpy caches per symbol (avoid re-creating arrays for each event)
        np5_cache = {}
        np1_cache = {}
        for sym in k5_cache:
            k5 = k5_cache[sym]
            np5_cache[sym] = (
                np.array([self._parse_ts(b['ts']).timestamp() for b in k5]),
                np.array([b['close'] for b in k5]),
            )
        for sym in k1_cache:
            k1 = k1_cache[sym]
            np1_cache[sym] = (
                np.array([self._parse_ts(b['ts']).timestamp() for b in k1]),
                np.array([b['high'] for b in k1]),
                np.array([b['low'] for b in k1]),
                np.array([b['close'] for b in k1]),
            )

        all_results = []  # (event_dt, symbol, direction_int, pnl_eff, mtm_dd_pct, entry_px, exit_px, direction, base_risk, exit_ts)
        for dt, sym, d in all_events:
            if sym not in k5_cache or sym not in k1_cache:
                continue
            result = self.simulate_trade(
                sym, dt, d,
                k5_cache[sym], k1_cache[sym],
                sl_pct=getattr(self, 'sl_pct', 0.05),
                tp_pct=getattr(self, 'tp_pct', 0.18),
                hold_h=getattr(self, 'hold_h', 120),
                pyr_trigger=getattr(self, "pyr_trigger", 0.05),
                pyr_add=getattr(self, "pyr_add", 0.5),
                trail_act=getattr(self, 'trail_act', 0.02),
                trail_dist=getattr(self, 'trail_dist', 0.015),
                trail_lock=getattr(self, 'trail_lock', 0.02),
                return_exit_ts=True,
                _cache_5m=np5_cache.get(sym),
                _cache_1m=np1_cache.get(sym),
            )
            if result is None:
                continue
            all_results.append((dt, sym, d, result))

        if not all_results:
            logger.error("Нет событий ни по одному символу")
            empty = {"summary": {"strategy": self.strategy_name, "tickers": [],
                                  "tf": self.tf_minutes, "days": self.days,
                                  "start_equity": self.initial_equity, "end_equity": self.initial_equity,
                                  "total_return": 0.0, "mdd": 0.0, "win_rate": 0.0,
                                  "profit_factor": float('inf'), "total_trades": 0, "calmar_ratio": 0.0},
                      "trades": [], "equity_curve": []}
            return empty

        # Build price lookup per symbol: {timestamp: close_price}
        price_map = {}
        for sym, bars in k1_cache.items():
            price_map[sym] = OrderedDict((self._parse_ts(b['ts']), b['close']) for b in bars)

# Build position intervals
        positions = []  # {entry_ts, exit_ts, symbol, direction, entry_px, base_risk, pnl_eff, mtm_dd_pct, eq_at_entry, opened}
        trades: list[dict] = []
        for dt, sym, d, res in all_results:
            pnl_eff, mtm_dd_pct, entry_px, exit_px, direction, base_risk, exit_ts = res
            positions.append({
                'entry_ts': dt, 'exit_ts': exit_ts,
                'symbol': sym, 'direction': direction,
                'entry_px': entry_px, 'exit_px': exit_px,
                'base_risk': base_risk,
                'pnl_eff': pnl_eff, 'mtm_dd_pct': mtm_dd_pct,
                'eq_at_entry': None,
                'entry_equity': None,  # фиксируется при открытии, не обнуляется
                'opened': False,
            })

        # ── 3. Portfolio MTM loop на 1m барах ──
        eq = float(self.initial_equity)
        cash = eq
        peak = eq
        max_dd = 0.0
        equity_curve: list[dict] = []

        # Все уникальные 1m таймстемпы по всем тикерам
        all_ts = sorted(set().union(*[price_map[s].keys() for s in price_map]))
        if not all_ts:
            logger.error("Нет 1m данных")
            return {"summary": {}, "trades": [], "equity_curve": []}

        next_pos_idx = 0  # следующая позиция к открытию
        positions.sort(key=lambda p: p['entry_ts'])  # сортируем по времени входа
        active: list[dict] = []  # открытые позиции (для MTM)

        equity_curve_sampled = []
        sample_every = max(1, len(all_ts) // 1000)  # семплируем ~1000 точек

        for bi, bar_ts in enumerate(all_ts):
            # ── Открываем новые позиции ──
            while next_pos_idx < len(positions):
                p = positions[next_pos_idx]
                if p['entry_ts'] <= bar_ts:
                    max_conc = getattr(self, 'max_conc', 999)
                    if len(active) < max_conc:
                        p['eq_at_entry'] = float(eq)
                        p['entry_equity'] = float(eq)
                        p['opened'] = True
                        active.append(p)
                    else:
                        p['_skipped'] = True
                    next_pos_idx += 1
                else:
                    break

            # ── Закрываем позиции с exit_ts < текущего бара ──
            still_active = []
            for p in active:
                if p['exit_ts'] < bar_ts:
                    # DEBUG: log first 3 closes
                    if not hasattr(self, '_debug_close'):
                        self._debug_close = {}
                    sym = p['symbol']
                    dc = self._debug_close.get(sym, 0)
                    if dc < 3:
                        self._debug_close[sym] = dc + 1
                        logger.info("DEBUG close %s: exit_ts=%s bar_ts=%s cmp=%s pnl_eff=%.4f", sym, p['exit_ts'], bar_ts, p['exit_ts'] < bar_ts, p['pnl_eff'])
                    r = self.risk_pct * self.SYM_RISK.get(p['symbol'], 1.0)
                    pnl_dollars = p['eq_at_entry'] * r * self.LEVERAGE * p['pnl_eff']
                    cash += pnl_dollars
                    p['eq_at_entry'] = None  # помечаем как закрытую
                else:
                    if not hasattr(self, '_debug_keep'):
                        self._debug_keep = {}
                    sym = p['symbol']
                    dk = self._debug_keep.get(sym, 0)
                    if dk < 3:
                        self._debug_keep[sym] = dk + 1
                        logger.info("DEBUG keep %s: exit_ts=%s bar_ts=%s cmp=%s pnl_eff=%.4f", sym, p['exit_ts'], bar_ts, p['exit_ts'] < bar_ts, p['pnl_eff'])
                    still_active.append(p)
            active = still_active

            # ── MTM стоимость портфеля по активным позициям ──
            port_value = cash
            for p in active:
                price = price_map[p['symbol']].get(bar_ts)
                if price is not None:
                    r = self.risk_pct * self.SYM_RISK.get(p['symbol'], 1.0)
                    if p['direction'] == 1:  # LONG
                        mtm_pnl = (price - p['entry_px']) / p['entry_px']
                    else:  # SHORT
                        mtm_pnl = (p['entry_px'] - price) / p['entry_px']
                    pos_value = p['eq_at_entry'] * r * self.LEVERAGE * mtm_pnl
                    port_value += pos_value

            eq = port_value

            # Трекинг пика и DD
            if port_value > peak:
                peak = port_value
            dd = (peak - port_value) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

            # Семплированная equity_curve
            if bi % sample_every == 0 or bi == len(all_ts) - 1:
                equity_curve_sampled.append({
                    "strategy": self.strategy_name,
                    "bar_time": str(bar_ts),
                    "equity": round(port_value, 2),
                    "cash_equity": round(cash, 2),
                    "drawdown": round(max_dd, 2),
                })

        # Закрываем оставшиеся открытые позиции (exit_ts после последнего бара)
        for p in positions:
            if p['opened'] and p['eq_at_entry'] is not None:
                r = self.risk_pct * self.SYM_RISK.get(p['symbol'], 1.0)
                pnl_dollars = p['eq_at_entry'] * r * self.LEVERAGE * p['pnl_eff']
                cash += pnl_dollars
                p['eq_at_entry'] = None

        # ── 4. Формируем отчёт о сделках ──
        for p in positions:
            if not p['opened'] or p.get('_skipped'):
                if p.get('_skipped'):
                    logger.debug("  %s: entry_ts=%s skipped (max_conc)", p['symbol'], p['entry_ts'])
                elif not p['opened']:
                    logger.warning("  %s: entry_ts=%s после последнего 1m бара, пропускаем", p['symbol'], p['entry_ts'])
                continue
            r = self.risk_pct * self.SYM_RISK.get(p['symbol'], 1.0)
            ee = p['entry_equity'] or self.initial_equity
            pnl_dollars = ee * r * self.LEVERAGE * p['pnl_eff']
            qty = ee * r * self.LEVERAGE * p['base_risk'] / p['entry_px'] if p['entry_px'] else 0
            trades.append({
                "strategy": self.strategy_name,
                "ticker": p['symbol'],
                "direction": 'LONG' if p['direction'] == 1 else 'SHORT',
                "entry_price": p['entry_px'],
                "exit_price": p['exit_px'],
                "entry_time": str(p['entry_ts']),
                "exit_time": str(p['exit_ts']),
                "quantity": qty,
                "pnl": pnl_dollars,
                "pnl_pct": p['pnl_eff'] * 100,
                "exit_reason": "sl_hit" if p['pnl_eff'] < 0 else "tp_hit",
                "tags": "{}",
            })

        # ── 5. Итоги ──
        total_return = (cash - self.initial_equity) / self.initial_equity * 100
        total_trades = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
        gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0) if wins > 0 else 0
        gross_loss = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0) if total_trades - wins > 0 else 0
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        calmar = total_return / max_dd if max_dd > 0 else 0.0

        summary = {
            "strategy": self.strategy_name,
            "params": json.dumps(getattr(self, 'strategy_params', {})),
            "tickers": [t["symbol"] for t in self.tickers if t["symbol"] not in self.EXCLUDE_SYMS],
            "tf": self.tf_minutes,
            "days": self.days,
            "start_equity": self.initial_equity,
            "end_equity": round(cash, 2),
            "total_return": round(total_return, 2),
            "mdd": round(max_dd, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2),
            "total_trades": total_trades,
            "calmar_ratio": round(calmar, 2),
        }

        logger.info("Итоги: eq=%.2f ret=%.2f%% DD=%.2f%% WR=%.1f%% trades=%d",
                    cash, total_return, max_dd, win_rate, total_trades)

        # ── 6. Сохраняем в PG ──
        if getattr(self, 'save_results', True):
            summary_id = self.pg.save_summary(summary)
            self.pg.save_trades_batch(trades, summary_id)
            self.pg.save_equity_points(equity_curve_sampled, summary_id)
        else:
            summary_id = 0

        return {"summary": summary, "summary_id": summary_id,
                "trades": trades, "equity_curve": equity_curve}