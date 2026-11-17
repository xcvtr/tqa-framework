"""Mock-биржа: стакан/позиции в памяти. Тот же интерфейс, что у MT5/Bybit.

Цены — только филлы: они попадают в сделки, но никогда в детектор.
`fill_price` по умолчанию не искажает цену (проскальзывание считает executor).
"""
from __future__ import annotations

from typing import Optional

from tqa_m.connector.exchange_base import (ExchangeBase, ExchangeConfig, Position,
                                           Signal)


class ExchangeMock(ExchangeBase):
    def __init__(self, config: ExchangeConfig | None = None, balance: float = 200_000.0):
        super().__init__(config or ExchangeConfig(name='mock'))
        self._prices: dict[str, float] = {}
        self._positions: list[Position] = []
        self._balance = float(balance)
        self._seq = 0

    # цены (в папере их двигает бар)
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = float(price)

    def get_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)

    def fill_price(self, symbol: str, side: str, ref_price: float) -> float:
        """Mock: исполнение по референсной цене без дополнительного импакта."""
        return float(ref_price)

    # позиции
    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def open_position(self, signal: Signal, quantity: float) -> Optional[Position]:
        price = self.get_price(signal.symbol) or float(signal.price)
        if not price:
            return None
        self._seq += 1
        pos = Position(symbol=signal.symbol, direction=signal.direction, entry_price=price,
                       current_price=price, quantity=quantity, entry_time=signal.timestamp,
                       id=str(self._seq))
        self._positions.append(pos)
        return pos

    def close_position(self, position: Position) -> bool:
        for i, p in enumerate(self._positions):
            if p.id == position.id:
                self._positions.pop(i)
                return True
        return False

    def get_account_balance(self) -> float:
        return self._balance
