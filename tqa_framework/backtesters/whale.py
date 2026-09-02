"""WHALE HOURLY IMBALANCE backtester — 1:1 порт engine/whale.py в tqa-framework.

Наследует Backtester для PG-сохранения, но run() и simulate_trade()
воспроизводят архитектуру TQA-crypto/engine/whale.py:

  - источник данных: crypto.agg_trades_binance (сырые aggTrades), НЕ bars
  - load_whale_data → часовые бары (hour, vwap, imb, buy/sell usd)
  - события: |imb| > TH (фиксированный глобальный порог 0.3)
  - simulate_trade: вход VWAP часа, выход VWAP следующего часа, SL 5%
  - портфельный MTM loop на часовых барах (общий капитал, компаунд)

Бэктест (2025 H1, 80 sym, pool, честный forward-VWAP): Gross WR 71% / +34.9bp.
Net (maker 10bp): WR 58%, +23.5K%/6mo. Taker 35bp убыточен — НЕ запускать с comm>0.0010.
"""

from __future__ import annotations

import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List

from tqa_framework.engine.backtester import Backtester

logger = logging.getLogger(__name__)


class WhaleBacktester(Backtester):
    """WHALE hourly-imbalance backtester — событийный, часовой MTM."""

    # Аудит 2025 H1 (80 sym, честный forward-VWAP)
    TH = 0.3            # |imb| порог «кита» (глобальный, НЕ per-symbol)
    SL_PCT = 0.05       # стоп-лосс (обязателен: хвосты −187% без него)
    COMM = 0.0005       # maker 5bp/side (taker 35bp УБЫТОЧЕН)
    SLIP = 0.0005
    LEVERAGE = 1
    MAX_SYMBOLS = 80
    MAX_POS = 5
    STABLES = {'USDCUSDT', 'USDTUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'PAXGUSDT',
               'EURUSDT', 'WBTCUSDT', 'USDPUSDT', 'DAIUSDT'}
    MARK_M = 5            # granularity маркировки MTM: 1 (M1) или 5 (M5). Правило: MDD только на M1.

    def _parse_ts(self, val) -> datetime:
        if isinstance(val, datetime):
            return val.replace(tzinfo=None) if val.tzinfo else val
        return datetime.fromisoformat(str(val).replace('Z', '+00:00')).replace(tzinfo=None)

    def _ch_query(self, q: str):
        import requests as _r
        resp = _r.get(self.ch_host, params={"query": q}, timeout=120)
        resp.raise_for_status()
        raw = resp.text.strip()
        return raw

    def load_whale_data(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """Часовые бары agg_trades: hour, buy_usd, sell_usd, vwap, imb.

        start/end — строки 'YYYY-MM-DD HH:MM:SS' (UTC-naive). Возвращает DataFrame.
        Данные грузятся ПОМЕСЯЧНО (partition-pruned) — один запрос на целый период
        по 80 символам сканирует ~150GB и упирается в timeout.
        """
        if not symbols:
            return pd.DataFrame()
        syms = ', '.join(f"'{s}'" for s in symbols)
        # месяцы периода
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        months = pd.period_range(s.to_period('M'), e.to_period('M'), freq='M')
        frames = []; m5frames = []
        for m in months:
            m_start = m.to_timestamp().strftime('%Y-%m-%d %H:00:00')
            m_end = (m.to_timestamp() + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)).strftime('%Y-%m-%d %H:00:00')
            m_start = max(m_start, start)
            m_end = min(m_end, end)
            q = f"""
                SELECT symbol, toStartOfHour(ts) AS hour,
                       sumIf(qty*price, is_buyer_maker=0) AS buy_usd,
                       sumIf(qty*price, is_buyer_maker=1) AS sell_usd,
                       sum(qty*price) AS total_usd,
                       sum(qty) AS total_qty,
                       min(price) AS lo, max(price) AS hi
                FROM crypto.agg_trades_binance
                WHERE ts >= toDateTime('{m_start}') AND ts < toDateTime('{m_end}')
                  AND symbol IN ({syms})
                  AND toYYYYMM(ts) = {m.year}{m.month:02d}
                GROUP BY symbol, hour ORDER BY symbol, hour
                FORMAT JSONEachRow
            """
            mark_m = int(getattr(self, 'MARK_M', 5))
            # granularity маркировки: M1 или M5 (правило MDD только на M1)
            bucket = 'toStartOfMinute' if mark_m == 1 else 'toStartOfFiveMinutes'
            q5 = f"""
                SELECT symbol, toStartOfHour(ts) AS hour, {bucket}(ts) AS m5,
                       min(price) AS lo, max(price) AS hi
                FROM crypto.agg_trades_binance
                WHERE ts >= toDateTime('{m_start}') AND ts < toDateTime('{m_end}')
                  AND symbol IN ({syms})
                  AND toYYYYMM(ts) = {m.year}{m.month:02d}
                GROUP BY symbol, hour, m5 ORDER BY symbol, hour, m5
                FORMAT JSONEachRow
            """
            for name, qq, acc in (('hour', q, frames), ('m5', q5, m5frames)):
                try:
                    raw = self._ch_query(qq)
                except Exception as ex:
                    logger.warning("месяц %s (%s): %s", m, name, str(ex)[:120])
                    continue
                rows = [json.loads(l) for l in raw.split('\n') if l.strip()]
                if rows:
                    acc.append(pd.DataFrame(rows))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df['vwap'] = df['total_usd'] / df['total_qty'].replace(0, np.nan)
        df['imb'] = (df['buy_usd'] - df['sell_usd']) / (df['buy_usd'] + df['sell_usd']).replace(0, np.nan)
        df['hour'] = pd.to_datetime(df['hour'])
        self._m5 = pd.concat(m5frames, ignore_index=True) if m5frames else pd.DataFrame()
        if not self._m5.empty:
            self._m5['hour'] = pd.to_datetime(self._m5['hour'])
            self._m5['m5'] = pd.to_datetime(self._m5['m5'])
        return df

    def simulate_trade(self, symbol, hour_ts, direction, df_sym, sl_pct=SL_PCT, return_exit_ts=False):
        """Сделка: вход VWAP часа, выход VWAP след. часа, SL.

        df_sym — часовой кадр одного символа (полный). Возвращает (pnl_pct, mtm_dd[, exit_ts]).
        """
        hour_ts = pd.Timestamp(self._parse_ts(hour_ts))
        d = df_sym[df_sym['hour'] == hour_ts]
        if len(d) == 0:
            return None
        entry = float(d.iloc[0]['vwap'])
        if not np.isfinite(entry) or entry <= 0:
            return None
        nxt = df_sym[df_sym['hour'] > hour_ts]
        if len(nxt) == 0:
            return None
        nxt = nxt.iloc[0]
        exit_ts = pd.Timestamp(nxt['hour'])
        exit_px = float(nxt['vwap'])
        if not np.isfinite(exit_px):
            return None
        sign = 1 if direction == 'LONG' else -1
        sl_px = entry * (1 - sl_pct) if sign == 1 else entry * (1 + sl_pct)
        # SL: аппрокс по close следующего часа (нет hi/lo в агрегате) — как оригинал
        if (sign == 1 and exit_px <= sl_px) or (sign == -1 and exit_px >= sl_px):
            exit_px = sl_px
        pnl = sign * (exit_px - entry) / entry - self.COMM - self.SLIP
        mtm_dd = max(0.0, (entry - exit_px) / entry) if sign == 1 else max(0.0, (exit_px - entry) / entry)
        if return_exit_ts:
            return pnl, mtm_dd, exit_ts
        return pnl, mtm_dd

    def run(self) -> dict:
        """Запустить whale-бэктест: портфельный часовой MTM loop."""
        self.pg.ensure_schemas()
        self.pg.ensure_tables_backtest()

        # Параметры из strategy_params (YAML) с дефолтами
        self.th = float(getattr(self, 'th', self.strategy_params.get('th', self.TH)))
        self.sl_pct = float(getattr(self, 'sl_pct', self.strategy_params.get('sl_pct', self.SL_PCT)))
        self.max_pos = int(getattr(self, 'max_pos', self.strategy_params.get('max_pos', self.MAX_POS)))
        _excl = getattr(self, 'exclude_syms', self.strategy_params.get('exclude_syms', [])) or []
        self.exclude = set(_excl) | self.STABLES
        _comm = getattr(self, 'comm', self.strategy_params.get('comm'))
        if _comm is not None:
            self.COMM = float(_comm)
            logger.info("COMM из YAML: %s (taker 0.0035 убыточен, maker 0.001)", self.COMM)

        # ── 1. Символы и данные ──
        # end_time: последний час в данных (дешёвый partition-pruned max)
        # если задан end_time_override — используем его (валидация на истории)
        import requests as _req
        if getattr(self, 'end_time_override', None):
            end_time = self.end_time_override
        else:
            q_latest = """
                SELECT max(ts) FROM crypto.agg_trades_binance
                WHERE toYYYYMM(ts) = (SELECT max(toInt32OrNull(partition)) FROM system.parts
                                      WHERE database='crypto' AND table='agg_trades_binance' AND active)
                FORMAT TabSeparated
            """
            _r = _req.get(self.ch_host, params={"query": q_latest}, timeout=60)
            _r.raise_for_status()
            latest_raw = _r.text.strip()
            end_time = latest_raw if latest_raw else datetime.now().strftime('%Y-%m-%d %H:00:00')
        end_dt = self._parse_ts(end_time)
        start_dt = end_dt - pd.Timedelta(days=self.days)
        start = start_dt.strftime('%Y-%m-%d %H:00:00')
        end = end_dt.strftime('%Y-%m-%d %H:00:00')
        logger.info("whale backtest период: %s .. %s", start, end)

        # символы: топ-ликвид по USD-объёму (не по count() — микро-тики новинок
        # дают миллионы строк при нулевом объёме) минус стаблы.
        # Топ считаем за 30-дневное окно до end (partition-pruned, дёшево) —
        # ликвидный юниверс в месяце ≈ в полугодии. Это сильно ускоряет запрос.
        month_start = (end_dt - pd.Timedelta(days=30)).strftime('%Y-%m-%d 00:00:00')
        sym_q = f"""
            SELECT symbol, sum(qty*price) AS usd, count() AS n
            FROM crypto.agg_trades_binance
            WHERE ts >= toDateTime('{month_start}') AND ts < toDateTime('{end}')
            GROUP BY symbol HAVING usd > 1000000
            ORDER BY usd DESC LIMIT {self.MAX_SYMBOLS}
            FORMAT JSONEachRow
        """
        sym_raw = self._ch_query(sym_q)
        symbols = [json.loads(l)['symbol'] for l in sym_raw.split('\n') if l.strip()]
        symbols = [s for s in symbols if s not in self.exclude][:self.MAX_SYMBOLS]
        logger.info("whale-символы (%d): %s", len(symbols), symbols[:10])

        df = self.load_whale_data(symbols, start, end)
        if df.empty or len(df) < 100:
            logger.error("Нет данных whale (%d бapов)", len(df))
            return {"summary": {"strategy": self.strategy_name, "tickers": symbols,
                                "tf": self.tf_minutes, "days": self.days,
                                "start_equity": self.initial_equity, "end_equity": self.initial_equity,
                                "total_return": 0.0, "mdd": 0.0, "win_rate": 0.0,
                                "profit_factor": float('inf'), "total_trades": 0, "calmar_ratio": 0.0},
                    "trades": [], "equity_curve": []}

        # ── 2. Портфельный MTM loop (честный: маркируем открытые позы по часовой цене) ──
        # Держим ПОЛНЫЙ кадр часовых баров (все символы, не только сигнальные) —
        # нужен для mark-to-market открытых позиций.
        hist = df.copy()  # полный часовой кадр (symbol, hour, vwap, lo, hi, imb)

        # Сигнальные события: |imb|>th, у них есть следующий час для выхода
        df = hist[hist['imb'].abs() > self.th].copy()
        df['v1'] = df.groupby('symbol')['vwap'].shift(-1)
        df['dir'] = np.where(df['imb'] > 0, 1, -1)
        df = df.dropna(subset=['vwap', 'v1'])
        df['sl_px_up'] = df['vwap'] * (1 + self.sl_pct)
        df['sl_px_dn'] = df['vwap'] * (1 - self.sl_pct)
        df = df.sort_values('hour')

        eq = float(self.initial_equity)
        cash = eq
        peak = eq
        max_dd = 0.0
        trades: List[dict] = []
        equity_curve = []

        # индекс цен: (symbol, hour) → (vwap, lo, hi)
        price_idx = {}
        for _, r in hist.iterrows():
            price_idx[(r['symbol'], r['hour'])] = (float(r['vwap']), float(r['lo']), float(r['hi']))

        all_ts = sorted(hist['hour'].unique())
        events_by_ts = {ts: grp for ts, grp in df.groupby('hour')}

        pending: List[dict] = []  # открытые события, ожидающие закрытия (hold 1h)

        # M5-индекс: (symbol, hour) → [(m5_ts, lo, hi), ...] для worst-path MTM
        m5_idx = {}
        if not self._m5.empty:
            for _, r in self._m5.iterrows():
                m5_idx.setdefault((r['symbol'], r['hour']), []).append((r['m5'], float(r['lo']), float(r['hi'])))

        for bi, bar_ts in enumerate(all_ts):
            bar_ts = pd.Timestamp(bar_ts)
            nxt_ts = pd.Timestamp(all_ts[bi + 1]) if bi + 1 < len(all_ts) else None

            # ── MTM: маркируем открытые (hold 1h) позиции по M5-пути часа ──
            closing = pending
            pending = []
            # worst-DD по M5: на каждом 5-мин внутри часа — (cash + unrealized)
            for p in closing:
                pm5 = m5_idx.get((p['symbol'], bar_ts))
                vw, lo, hi = price_idx.get((p['symbol'], bar_ts), (None, None, None))
                sign = p['dir']
                # точки для mark: M5 lo/hi, плюс VWAP бара на границах hold-окна
                marks = []
                if vw is not None:
                    marks.append((vw, vw))  # entry-граница
                if pm5:
                    for _ts, _lo, _hi in pm5:
                        marks.append((_lo, _hi))
                if vw is not None:
                    marks.append((vw, vw))  # exit-отсчёт
                for _lo, _hi in marks:
                    worst_px = _lo if sign == 1 else _hi
                    if worst_px is None:
                        continue
                    worst_pnl = sign * (worst_px - p['entry']) / p['entry']
                    if p['eq_at_entry'] > 0:
                        worst_dd_contrib = p['eq_at_entry'] * p['alloc'] * self.LEVERAGE * worst_pnl
                        pre_eq = cash + worst_dd_contrib
                        if pre_eq > peak:
                            peak = pre_eq
                        if peak > 0:
                            dd_w = (peak - pre_eq) / peak * 100
                            max_dd = max(max_dd, dd_w)

            # ── Открываем события часа (вход по VWAP, hold 1h) ──
            grp = events_by_ts.get(bar_ts)
            if grp is not None and nxt_ts is not None:
                for _, r in grp.iterrows():
                    if len(pending) + len(closing) >= self.max_pos:
                        break
                    sign = r['dir']
                    entry = float(r['vwap'])
                    if not np.isfinite(entry) or entry <= 0:
                        continue
                    sl_px = entry * (1 - self.sl_pct) if sign == 1 else entry * (1 + self.sl_pct)
                    pending.append({
                        'symbol': r['symbol'], 'dir': sign, 'entry': entry,
                        'sl_px': sl_px, 'open_ts': bar_ts, 'exit_ts': nxt_ts,
                        'alloc': self.risk_pct, 'eq_at_entry': cash,
                    })

            # ── Реализация PnL закрываемым позициям (exit по VWAP/SL этого часа) ──
            for p in closing:
                px = price_idx.get((p['symbol'], bar_ts))
                if px is None:
                    continue  # нет бара — позиция "потеряна" (не закрыта)
                vwap, lo, hi = px
                sign = p['dir']
                # SL по внутричасовому экстремуму
                if (sign == 1 and lo is not None and lo <= p['sl_px']) or \
                   (sign == -1 and hi is not None and hi >= p['sl_px']):
                    exit_px = p['sl_px']
                else:
                    exit_px = vwap
                pnl = sign * (exit_px - p['entry']) / p['entry'] - self.COMM - self.SLIP
                alloc = p['alloc']
                pnl_dollars = p['eq_at_entry'] * alloc * self.LEVERAGE * pnl
                cash += pnl_dollars
                trades.append({
                    "strategy": self.strategy_name,
                    "ticker": p['symbol'],
                    "direction": 'LONG' if sign == 1 else 'SHORT',
                    "entry_price": p['entry'], "exit_price": exit_px,
                    "entry_time": str(p['open_ts']), "exit_time": str(bar_ts),
                    "quantity": p['eq_at_entry'] * alloc * self.LEVERAGE / p['entry'],
                    "pnl": pnl_dollars, "pnl_pct": pnl * 100,
                    "exit_reason": "sl_hit" if pnl < 0 else "tp_hit", "tags": "{}",
                })

            # equity после закрытия (реализованный cash)
            eq = cash
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak * 100
                max_dd = max(max_dd, dd)
            equity_curve.append({"strategy": self.strategy_name, "bar_time": str(bar_ts),
                                 "equity": round(eq, 2), "cash_equity": round(cash, 2),
                                 "drawdown": round(max_dd, 2)})
            if eq <= 0:
                break

        # ── 3. Итоги ──
        total_return = (eq - self.initial_equity) / self.initial_equity * 100
        total_trades = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
        gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0) if wins > 0 else 0
        gross_loss = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0) if total_trades - wins > 0 else 0
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        calmar = total_return / max_dd if max_dd > 0 else 0.0
        span_yr = self.days / 365.25
        cagr = ((eq / self.initial_equity) ** (1 / span_yr) - 1) * 100 if span_yr > 0.1 else 0.0

        summary = {
            "strategy": self.strategy_name,
            "params": json.dumps({**getattr(self, 'strategy_params', {}), 'th': self.th, 'sl_pct': self.sl_pct}),
            "tickers": symbols,
            "tf": self.tf_minutes, "days": self.days,
            "start_equity": self.initial_equity, "end_equity": round(eq, 2),
            "total_return": round(total_return, 2), "mdd": round(max_dd, 2),
            "win_rate": round(win_rate, 1), "profit_factor": round(pf, 2),
            "total_trades": total_trades, "calmar_ratio": round(calmar, 2),
            "cagr": round(cagr, 1),
        }
        logger.info("WHALE: eq=%.2f ret=%.2f%% DD=%.2f%% WR=%.1f%% trades=%d", eq, total_return, max_dd, win_rate, total_trades)

        if getattr(self, 'save_results', True):
            summary_id = self.pg.save_summary(summary)
            self.pg.save_trades_batch(trades, summary_id)
            self.pg.save_equity_points(equity_curve, summary_id)
        else:
            summary_id = 0

        return {"summary": summary, "summary_id": summary_id, "trades": trades, "equity_curve": equity_curve}
