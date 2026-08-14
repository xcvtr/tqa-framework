"""ABC: Position, Signal, ExchangeBase.

Все executor'ы наследуют ExchangeBase.
Новый рынок = новый класс. Иммутабельно — не менять прод.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Optional


@dataclass
class Position:
    """Открытая позиция."""
    symbol: str
    direction: str          # 'LONG' | 'SHORT'
    entry_price: float
    current_price: float
    quantity: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    trail_activation: Optional[float] = None
    trail_distance: float = 0.0
    bars_held: int = 0
    entry_time: Optional[str] = None
    id: Optional[str] = None


@dataclass
class Signal:
    """Торговый сигнал от стратегии."""
    symbol: str
    direction: str          # 'LONG' | 'SHORT'
    price: float
    timestamp: str
    strategy: str = ""
    score: float = 1.0
    confidence: float = 1.0
    reason: str = ""
    day_net: Optional[float] = None  # для OI: дневной дисбаланс на момент сигнала


@dataclass
class ExchangeConfig:
    """Конфиг подключения к бирже."""
    name: str
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    params: dict = field(default_factory=dict)


class ExchangeBase(ABC):
    """Абстрактная биржа. Наследовать для каждого рынка."""

    def __init__(self, config: ExchangeConfig):
        self.config = config

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Текущая цена."""
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Открытые позиции."""
        ...

    @abstractmethod
    def open_position(self, signal: Signal, quantity: float) -> Optional[Position]:
        """Открыть позицию."""
        ...

    @abstractmethod
    def close_position(self, position: Position) -> bool:
        """Закрыть позицию."""
        ...

    @abstractmethod
    def get_account_balance(self) -> float:
        """Баланс счёта (equity)."""
        ...
