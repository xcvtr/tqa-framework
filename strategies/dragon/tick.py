"""Dragon tick — per-tick: SL, trailing, timeout, открытие.

Вызывается каждую минуту (cron).
"""

from __future__ import annotations

from engine.exchange_base import Position


def evaluate_position(position: Position, price: float,
                      config: dict) -> str:
    """Проверить позицию на выход.

    Returns:
        'hold' | 'sl' | 'trailing' | 'timeout' | 'tp'
    """
    raise NotImplementedError
