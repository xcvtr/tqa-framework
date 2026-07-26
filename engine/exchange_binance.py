"""Live Binance executor (crypto). IMMUTABLE — не трогать прод.

Новый сценарий = новый класс, не менять этот файл.
"""

from __future__ import annotations

from engine.exchange_base import (
    ExchangeBase, ExchangeConfig, Position, Signal,
)


class ExchangeBinance(ExchangeBase):
    """Binance live API. Только bugfix."""

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        # TODO: init Binance client

    def get_price(self, symbol: str) -> float:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    def open_position(self, signal: Signal, quantity: float) -> Position:
        raise NotImplementedError

    def close_position(self, position: Position) -> bool:
        raise NotImplementedError

    def get_account_balance(self) -> float:
        raise NotImplementedError
