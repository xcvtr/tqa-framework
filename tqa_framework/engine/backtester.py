"""Портфельный бэктестер — общий для всех стратегий.

Перебирает бары, вызывает detect() и tick() из стратегии.
Результаты сохраняет в PG (backtest.trades, equity_curve, summary).

Контракт стратегии:
    strategies/<name>/detect.py → detect(bars, config) → list[Signal]
        bars: list[dict] с ключами ts, open, high, low, close, volume
        config: dict (полный конфиг из YAML)

    strategies/<name>/tick.py → evaluate_position(position, price, config) → str
        position: Position (exchange_base)
        price: float (текущая цена)
        returns: 'hold' | 'sl' | 'tp' | 'trailing' | 'timeout'
"""
from __future__ import annotations

import importlib
import json
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from tqa_framework.engine.exchange_base import Position, Signal
from tqa_framework.engine.detect import load_m1_from_ch, resample_bars, dedup_signals
from tqa_framework.engine.risk import calc_risk_mult, calc_contracts, sma_50_trend
from tqa_framework.engine.pg_state import PGState

logger = logging.getLogger(__name__)


class Backtester:
    """Универсальный бэктестер. Работает с любой стратегией."""

    def __init__(
        self,
        tickers: list[dict],
        days: int,
        risk_pct: float,
        tf_minutes: int,
        strategy_name: str,
        strategy_params: Optional[dict] = None,
        initial_equity: float = 100_000.0,
        pg: Optional[PGState] = None,
        ch_host: str = "",
        ch_db: str = "",
        max_conc: int = 6,
        strategy_path: str = "",
        market: str = "moex",
        point: Optional[dict] = None,
        pip_value: float = 10.0,
        commission_per_lot: float = 0.0,
        spread_points: Optional[dict] = None,
        swap_per_night: Optional[dict] = None,
        detect_every: int = 1,
        save_results: bool = True,
        end_time: str = "",
    ):
        """Бэктестер.

        market='moex'  — pnl = (exit-entry) × quantity (контракты, комиссия 8₽)
        market='forex' — pnl = (exit-entry)/point × pip_value × lot + комиссия + своп

        point: dict {symbol: point} — пункт MT5 (EURUSD=1e-5, USDJPY=0.001, XAU=0.01)
        pip_value: $ за пункт на 1 лот (AlfaForex: 10.0)
        commission_per_lot: $ за 1 лот round-trip
        spread_points: dict {symbol: spread в пунктах} — вычитается из pnl
        swap_per_night: dict {symbol: (long_swap, short_swap) в пунктах} — за ночь
        """
        self.tickers = tickers
        self.days = days
        self.risk_pct = risk_pct
        self.tf_minutes = tf_minutes
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params or {}
        self.pyramid_max = self.strategy_params.get("pyramid_max", 1)
        self.strategy_path = strategy_path
        self.initial_equity = initial_equity
        self.pg = pg or PGState()
        self.ch_host = ch_host
        self.ch_db = ch_db
        self.max_conc = max_conc
        self.market = market
        self.detect_every = detect_every
        self.save_results = save_results
        self.end_time_override = end_time
        self.point = point or {}
        self.pip_value = pip_value
        self.commission_per_lot = commission_per_lot
        self.spread_points = spread_points or {}
        self.swap_per_night = swap_per_night or {}

        self._detect_fn: Optional[Callable] = None
        self._tick_fn: Optional[Callable] = None

    def _load_strategy(self):
        """Загрузить detect/tick из стратегии через importlib.

        Если указан strategy_path — добавляет его в sys.path.
        Поддерживает два формата:
          1) path/strategies/<name>/detect.py (корень репозитория)
          2) path/<name>/detect.py              (папка strategies напрямую)
        """
        import os, sys
        path = getattr(self, 'strategy_path', None)
        if path and os.path.isdir(path):
            sys.path.insert(0, path)

        # Попробовать strategies.<name>.detect
        for mod_name in [f"strategies.{self.strategy_name}",
                          self.strategy_name]:
            try:
                detect_mod = importlib.import_module(f"{mod_name}.detect")
                self._detect_fn = detect_mod.detect
                tick_mod = importlib.import_module(f"{mod_name}.tick")
                self._tick_fn = tick_mod.evaluate_position
                return
            except ImportError:
                continue

        raise ImportError(
            f"Стратегия '{self.strategy_name}' не найдена. "
            f"Проверялись пути: strategies/{self.strategy_name} и {self.strategy_name}. "
            f"strategy_path={path}. "
            f"Ожидается: strategies/{self.strategy_name}/detect.py и tick.py"
        )

    def run(self) -> dict:
        """Запустить бэктест для всех тикеров.

        Returns:
            dict с ключами:
                summary — сводка
                trades — список сделок
                equity_curve — кривая капитала
        """
        self._load_strategy()
        self.pg.ensure_schemas()
        self.pg.ensure_tables_backtest()

        # Зафиксировать конец периода — последний бар в CH
        import requests as _req
        if self.ch_db == "forex":
            _sym0 = self.tickers[0]["symbol"]
            _q = (f"SELECT max(time) FROM forex.bars WHERE symbol='{_sym0}' FORMAT TabSeparated")
        else:
            _q = f"SELECT max(bt) FROM {self.ch_db}.bars FORMAT TabSeparated"
        _r = _req.get(self.ch_host, params={"query": _q}, timeout=15)
        _r.raise_for_status()
        ch_latest = _r.text.strip()
        end_time = self.end_time_override if self.end_time_override else (ch_latest if ch_latest else "")
        if end_time:
            logger.info("CH последний бар: %s", end_time)
        else:
            logger.warning("Не удалось определить последний бар, используется now()")

        equity = self.initial_equity
        peak = equity
        self._realized = 0.0
        all_trades: list[dict] = []
        all_equity: list[dict] = []
        positions: list[dict] = []  # открытые позиции

        # ── ПОРТФЕЛЬ: загружаем ВСЕ символы, синхронизируем по времени ──
        sym_bars = {}   # symbol -> list[dict] M1
        sym_tf = {}     # symbol -> resampled bars
        sym_rpct = {}   # symbol -> risk_pct
        for ticker_cfg in self.tickers:
            symbol = ticker_cfg["symbol"]
            tf = ticker_cfg.get("tf", self.tf_minutes)
            rpct = ticker_cfg.get("risk_pct", self.risk_pct)

            logger.info("Загрузка %s за %d дней до %s...",
                        symbol, self.days, end_time or "now()")
            bars = load_m1_from_ch(
                symbol, self.days * 24,
                self.ch_host, self.ch_db,
                end_time=end_time,
            )
            if not bars:
                logger.warning("Нет данных для %s", symbol)
                continue
            tf_bars = resample_bars(bars, tf)
            logger.info("  → %d баров на TF=%d", len(tf_bars), tf)
            sym_bars[symbol] = tf_bars
            sym_tf[symbol] = tf
            sym_rpct[symbol] = rpct

        if not sym_bars:
            logger.error("Нет данных ни по одному символу")
            return {"summary": {}, "trades": [], "equity_curve": []}

        # Общий временной индекс: union всех bar_time (отсортированный)
        all_times = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
        # индекс бара по времени для каждого символа
        sym_idx = {}
        for symbol, bars in sym_bars.items():
            sym_idx[symbol] = {b["ts"]: i for i, b in enumerate(bars)}

        detect_lookback = getattr(self, "detect_lookback", 600)
        _last_prog = 0
        # ── ЕДИНЫЙ ПОРТФЕЛЬНЫЙ ЦИКЛ: каждый bar_time обрабатываем ВСЕ символы ──
        for ti, bar_time in enumerate(all_times):
            if ti // 50000 > _last_prog:
                _last_prog = ti // 50000
                print(f"  портфель: бар {ti}/{len(all_times)}", flush=True)

            # текущие цены всех символов на этом bar_time
            prices = {}
            for symbol, bars in sym_bars.items():
                idx = sym_idx[symbol].get(bar_time)
                if idx is not None and idx < len(bars):
                    prices[symbol] = bars[idx]["close"]

            # ── 1. Тик: проверка ВСЕХ открытых позиций (по текущим ценам) ──
            for pos_dict in [p for p in positions if not p.get("closed")]:
                sym = pos_dict["symbol"]
                price = prices.get(sym)
                if price is None:
                    continue
                pos = Position(
                    symbol=sym,
                    direction=pos_dict["direction"],
                    entry_price=pos_dict["entry_price"],
                    current_price=price,
                    quantity=pos_dict["quantity"],
                    sl_price=pos_dict.get("sl_price"),
                    tp_price=pos_dict.get("tp_price"),
                    trail_activation=pos_dict.get("trail_activation"),
                    trail_distance=pos_dict.get("trail_distance", 0.0),
                    entry_time=pos_dict.get("entry_time"),
                )
                reason = self._tick_fn(pos, price, self.strategy_params)
                if reason != "hold":
                    self._close_position(pos_dict, price, bar_time, reason, all_trades)

            # ── 2. Детект: ВСЕ символы на этом баре ──
            if ti >= 10 and (ti % self.detect_every == 0 or ti == len(all_times) - 1):
                for symbol, bars in sym_bars.items():
                    idx = sym_idx[symbol].get(bar_time)
                    if idx is None or idx < 10:
                        continue
                    window = bars[max(0, idx - detect_lookback + 1):idx + 1]
                    signals = self._detect_fn(window, {
                        **self.strategy_params,
                        "symbol": symbol,
                        "tf": sym_tf[symbol],
                        "risk_pct": sym_rpct[symbol],
                    })
                    for sig in signals:
                        if len([p for p in positions if not p.get("closed")]) >= self.max_conc:
                            break
                        # Пирамидинг: до pyramid_max позиций на символ (одного направления)
                        open_sym = [p for p in positions
                                    if not p.get("closed") and p["symbol"] == sig.symbol]
                        if len(open_sym) >= self.pyramid_max:
                            continue
                        if open_sym and any(p["direction"] != sig.direction for p in open_sym):
                            continue  # не открываем противоположное направление на тот же символ
                        if not open_sym and any(not p.get("closed") and p["symbol"] == sig.symbol
                                                for p in positions):
                            # обычный режим (pyramid_max=1): одна позиция на символ
                            if self.pyramid_max == 1:
                                continue
                        # КОНКУРЕНЦИЯ ЗА КАПИТАЛ
                        active = [p for p in positions if not p.get("closed")]
                        used_risk = sum(p.get("_risk_amount", 0.0) for p in active)
                        free_equity = max(equity - used_risk, equity * 0.1)
                        pos = self._open_position(sig, free_equity, sym_rpct[symbol], bar_time)
                        if pos:
                            positions.append(pos)

            # ── 3. Equity: MTM всех позиций по текущим ценам ──
            active = [p for p in positions if not p.get("closed")]
            unrealized = 0.0
            for p in active:
                price = prices.get(p["symbol"])
                if price is not None:
                    unrealized += self._mtm(p, price)
                else:
                    unrealized += self._mtm_last(p)
            cash_equity = self.initial_equity + self._realized
            mtm_equity = cash_equity + unrealized
            equity = mtm_equity
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100 if peak > 0 else 0

            all_equity.append({
                "strategy": self.strategy_name,
                "bar_time": bar_time,
                "equity": round(mtm_equity, 2),
                "cash_equity": round(cash_equity, 2),
                "drawdown": round(dd, 2),
            })

        # Закрыть оставшиеся позиции по последней цене
        if all_equity:
            last_bar_time = all_equity[-1]["bar_time"]
        else:
            last_bar_time = ""
        for pos in positions:
            if not pos.get("closed"):
                last_price = pos.get("_last_price", pos["entry_price"])
                self._close_position(pos, last_price, last_bar_time, "timeout", all_trades)

        # --- Итоги ---
        summary = self._calc_summary(all_trades, all_equity, equity)

        # Сохранить в PG (summary первой, чтобы получить id)
        if getattr(self, "save_results", True):
            summary_id = self.pg.save_summary(summary)
            self.pg.save_trades_batch(all_trades, summary_id)
            self.pg.save_equity_points(all_equity, summary_id)
        else:
            summary_id = 0

        return {"summary": summary, "summary_id": summary_id,
                "trades": all_trades, "equity_curve": all_equity}

    # --- Внутренние методы ---

    def _open_position(
        self, signal: Signal, equity: float, risk_pct: float, bar_time: str
    ) -> Optional[dict]:
        """Открыть позицию по сигналу."""
        risk_mult = calc_risk_mult(equity)
        sl_pips = max(self.strategy_params.get("sl_pips", 100.0), 10.0)
        if self.market == "forex":
            # Лот от риска: риск $ = equity × risk_pct; SL в пипсах → лот
            rp = risk_pct
            # Dynamic risk: 100% на старте → 75% по мере роста (skill dynamic-risk-sizing)
            dr = self.strategy_params.get("dynamic_risk", 0.0)
            if dr > 0 and self.initial_equity > 0:
                base = risk_pct * 0.75
                rp = base + (risk_pct - base) * math.exp(-dr * (equity / self.initial_equity - 1))
            qty = max(0.01, round(equity * rp * risk_mult / (sl_pips * self.pip_value), 2))
        else:
            qty = calc_contracts(equity, risk_pct * risk_mult, signal.price, signal.price)
        if qty <= 0:
            return None
        # риск в $: qty × SL × pip_value
        risk_amount = qty * sl_pips * self.pip_value
        return {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.price,
            "quantity": qty,
            "_risk_amount": risk_amount,
            "entry_time": bar_time,
            "sl_price": None,
            "tp_price": None,
            "trail_activation": None,
            "trail_distance": 0.0,
            "strategy": self.strategy_name,
            "closed": False,
            "exit_price": None,
            "exit_time": None,
            "_last_price": signal.price,
            "_entry_dt": bar_time,
            "_swap_acc": 0.0,
        }

    def _close_position(
        self, pos: dict, price: float, bar_time: str,
        reason: str, trades: list[dict],
    ):
        """Закрыть позицию и записать сделку."""
        direction = pos["direction"]
        if self.market == "forex":
            sym = pos["symbol"].lower()
            pt = self.point.get(sym, 1e-5)
            pip = 10 * pt  # pip = 10 × point (AlfaForex)
            pnl = (price - pos["entry_price"]) / pip * self.pip_value * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            # спред (в пунктах) — платим при входе+выходе
            spr = self.spread_points.get(sym, 0.0)
            # спред: spread_points в $ на лот (Rann: EURUSD 6 = $6/лот round-trip)
            pnl -= spr * pos["quantity"]
            # комиссия
            pnl -= self.commission_per_lot * pos["quantity"]
            # свопы за ночь
            pnl -= pos.get("_swap_acc", 0.0)
            pnl_pct = pnl / (pos["entry_price"] * pos["quantity"]) * 100
        else:
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            pnl_pct = pnl / (pos["entry_price"] * pos["quantity"]) * 100

        trades.append({
            "strategy": self.strategy_name,
            "ticker": pos["symbol"],
            "direction": pos["direction"],
            "entry_time": pos["entry_time"],
            "exit_time": bar_time,
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "quantity": round(pos["quantity"], 6),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": reason,
            "tags": '[]',
        })
        self._realized = getattr(self, "_realized", 0.0) + pnl
        pos["closed"] = True
        pos["exit_price"] = price
        pos["exit_time"] = bar_time

    def _mtm(self, pos: dict, price: float) -> float:
        """MTM по текущей цене + обновление лучшей цены для трейлинга."""
        pos["_last_price"] = price
        direction = pos["direction"]
        # обновляем trail_activation (лучшая цена)
        if direction == "LONG":
            if pos.get("trail_activation") is None or price > pos["trail_activation"]:
                pos["trail_activation"] = price
        else:
            if pos.get("trail_activation") is None or price < pos["trail_activation"]:
                pos["trail_activation"] = price
        if self.market == "forex":
            sym = pos["symbol"].lower()
            pt = self.point.get(sym, 1e-5)
            pip = 10 * pt  # pip = 10 × point (AlfaForex)
            pnl = (price - pos["entry_price"]) / pip * self.pip_value * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            # накопление свопа (грубо: каждый тик = ночь, только для дневных баров)
            return pnl - pos.get("_swap_acc", 0.0)
        pnl = (price - pos["entry_price"]) * pos["quantity"]
        if direction == "SHORT":
            pnl = -pnl
        return pnl

    def _mtm_last(self, pos: dict) -> float:
        """MTM по последней известной цене."""
        price = pos.get("_last_price", pos["entry_price"])
        return self._mtm(pos, price)

    def _calc_summary(
        self, trades: list[dict], equity_curve: list[dict],
        final_equity: float,
    ) -> dict:
        """Рассчитать итоговую сводку."""
        total_return = (final_equity - self.initial_equity) / self.initial_equity * 100
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_trades = len(trades)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        mdd = min((p["drawdown"] for p in equity_curve), default=0)
        calmar = total_return / abs(mdd) if mdd != 0 else 0

        return {
            "strategy": self.strategy_name,
            "tickers": [t.get("symbol", "") for t in self.tickers],
            "tf": self.tf_minutes,
            "days": self.days,
            "start_equity": self.initial_equity,
            "end_equity": round(final_equity, 2),
            "total_return": round(total_return, 2),
            "mdd": round(abs(mdd), 2),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_trades": total_trades,
            "calmar_ratio": round(calmar, 2),
            "params": json.dumps({k: v for k, v in self.strategy_params.items()
                                  if k != "dom_series"}, default=str),
        }
