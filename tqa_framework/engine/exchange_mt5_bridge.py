"""Live MT5 bridge executor (forex/CFD). IMMUTABLE — не трогать прод.

Новый сценарий = новый класс, не менять этот файл.
"""

from __future__ import annotations

from tqa_framework.engine.exchange_base import (
    ExchangeBase, ExchangeConfig, Position, Signal,
)


class ExchangeMT5(ExchangeBase):
    """MT5 terminal bridge (forex/CFD). Только bugfix."""

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        # TODO: init MT5 bridge client

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
