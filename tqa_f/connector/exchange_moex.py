"""ExchangeMoexMock — эмуляция MOEX-исполнения на M1 из CH mt5_roll.

Исполнительный слой (слой №2 — connector). Цены филлов по закрытым барам
(как канон backtest.py), лендинг → PG state, никогда в детектор.

Адаптация ExchangeBase (крипта) под MOEX-фьючерсы:
  • тикер — контракт из CH mt5_roll;
  • PnL (руб): ticks * step_price * shares, где ticks = (px-entry)/min_step;
  • комиссия per-ticker round-trip из ticker_specs.fee_entry (₽/контракт);
  • ГО/КНУР-лимит из ticker_specs (go) — размер позы ограничен долей equity.
"""
from __future__ import annotations

from typing import Optional

from tqa_f.connector.exchange_base import (
    ExchangeBase, ExchangeConfig, Position, Signal,
)


class ExchangeMoexMock(ExchangeBase):
    """Без исполнения на рынке: филл по закрытому бару + расчёт PnL/комиссии."""

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._trades: list[dict] = []   # закрытые позиции (лендинг в PG state)

    # ── спецификации тикера (min_step/step_price/fee/go) ──────────────────────
    @staticmethod
    def _spec(min_step: float, step_price: float, fee_entry: float,
              go: float, pct: float = 1.0) -> dict:
        return {
            'min_step': min_step, 'step_price': step_price,
            'fee_entry': fee_entry, 'go': go, 'pct': pct,
        }

    # ── ExchangeBase API (крипта → MOEX; здесь только контрактный скелет,
    #    филл/расчёт намеренно НЕ здесь — единый executor) ─────────────────────
    def get_price(self, symbol: str) -> float:
        raise NotImplementedError("MoexMock: филл по закрытым барам в executor")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("MoexMock: пеерсист в PG multi_state")

    def open_position(self, signal: Signal, quantity: float) -> Optional[Position]:
        raise NotImplementedError("MoexMock: open через executor.process_signal")

    def close_position(self, position: Position) -> bool:
        raise NotImplementedError("MoexMock: close через executor._close_market")

    def get_account_balance(self) -> float:
        raise NotImplementedError("MoexMock: equity из PG state")