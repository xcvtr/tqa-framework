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
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from engine.exchange_base import Position, Signal
from engine.detect import load_m1_from_ch, resample_bars, dedup_signals
from engine.risk import calc_risk_mult, calc_contracts, sma_50_trend
from engine.pg_state import PGState

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
    ):
        self.tickers = tickers
        self.days = days
        self.risk_pct = risk_pct
        self.tf_minutes = tf_minutes
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params or {}
        self.strategy_path = strategy_path
        self.initial_equity = initial_equity
        self.pg = pg or PGState()
        self.ch_host = ch_host
        self.ch_db = ch_db
        self.max_conc = max_conc

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
        _r = _req.get(self.ch_host, params={
            "query": f"SELECT max(bt) FROM {self.ch_db}.bars FORMAT TabSeparated"
        }, timeout=15)
        _r.raise_for_status()
        ch_latest = _r.text.strip()
        end_time = ch_latest if ch_latest else ""
        if end_time:
            logger.info("CH последний бар: %s", end_time)
        else:
            logger.warning("Не удалось определить последний бар, используется now()")

        equity = self.initial_equity
        peak = equity
        all_trades: list[dict] = []
        all_equity: list[dict] = []
        positions: list[dict] = []  # открытые позиции

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

            # Скользящее окно по барам
            for i in range(1, len(tf_bars) + 1):
                window = tf_bars[:i]
                current = window[-1]
                price = current["close"]
                bar_time = current["ts"]

                # --- 1. Тик: проверка открытых позиций ---
                open_positions = [p for p in positions if p["symbol"] == symbol]
                for pos_dict in open_positions:
                    pos = Position(
                        symbol=pos_dict["symbol"],
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

                # --- 2. Детект: поиск сигналов ---
                if i >= 10:  # минимальная прогретая зона
                    signals = self._detect_fn(window, {
                        **self.strategy_params,
                        "symbol": symbol,
                        "tf": tf,
                        "risk_pct": rpct,
                    })

                    # Открыть новые по сигналам
                    for sig in signals:
                        if len([p for p in positions if not p.get("closed")]) >= self.max_conc:
                            break
                        pos = self._open_position(sig, equity, rpct, bar_time)
                        if pos:
                            positions.append(pos)

                # --- 3. Equity ---
                active = [p for p in positions if not p.get("closed")]
                mtm_pnl = sum(self._mtm(p, price) for p in active if p["symbol"] == symbol)
                other_pnl = sum(self._mtm_last(p) for p in active if p["symbol"] != symbol)
                unrealized = mtm_pnl + other_pnl
                realized = sum(t["pnl"] for t in all_trades)
                cash_equity = self.initial_equity + realized
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
        summary_id = self.pg.save_summary(summary)
        self.pg.save_trades_batch(all_trades, summary_id)
        self.pg.save_equity_points(all_equity, summary_id)

        return {"summary": summary, "summary_id": summary_id,
                "trades": all_trades, "equity_curve": all_equity}

    # --- Внутренние методы ---

    def _open_position(
        self, signal: Signal, equity: float, risk_pct: float, bar_time: str
    ) -> Optional[dict]:
        """Открыть позицию по сигналу."""
        risk_mult = calc_risk_mult(equity)
        qty = calc_contracts(equity, risk_pct * risk_mult, signal.price, signal.price)
        if qty <= 0:
            return None
        return {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.price,
            "quantity": qty,
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
        }

    def _close_position(
        self, pos: dict, price: float, bar_time: str,
        reason: str, trades: list[dict],
    ):
        """Закрыть позицию и записать сделку."""
        direction = pos["direction"]
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
        pos["closed"] = True
        pos["exit_price"] = price
        pos["exit_time"] = bar_time

    def _mtm(self, pos: dict, price: float) -> float:
        """MTM по текущей цене."""
        pos["_last_price"] = price
        direction = pos["direction"]
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
            "params": json.dumps(self.strategy_params),
        }
