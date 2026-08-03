"""Mock биржа — эмуляция на 1m барах из CH/PG.

Используется в backtest: те же detect() + evaluate_position(),
но с ExchangeMock вместо live executor'а.
"""

from __future__ import annotations

from tqa_framework.engine.exchange_base import (
    ExchangeBase, ExchangeConfig, Position, Signal,
)


class ExchangeMock(ExchangeBase):
    """Эмулятор биржи на исторических данных из CH/PG."""

    def __init__(self, config: ExchangeConfig, bars_data: dict):
        super().__init__(config)
        self.bars = bars_data  # symbol -> list[OHLCV]
        self._idx = 0
        self._positions: list[Position] = []

    def get_price(self, symbol: str) -> float:
        raise NotImplementedError("Mock: используй iter_bars + evaluate_position")

    def get_positions(self) -> list[Position]:
        return self._positions

    def open_position(self, signal: Signal, quantity: float) -> Position:
        raise NotImplementedError("Mock: open через evaluate_position")

    def close_position(self, position: Position) -> bool:
        self._positions = [p for p in self._positions if p.id != position.id]
        return True

    def get_account_balance(self) -> float:
        return 0.0
