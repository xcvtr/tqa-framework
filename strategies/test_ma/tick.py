"""Test tick — SL/TP по конфигу."""
from __future__ import annotations

from engine.exchange_base import Position


def evaluate_position(position: Position, price: float, config: dict) -> str:
    """Проверить позицию на выход."""
    direction = position.direction
    sl_pct = config.get("sl_pct", 0.02)
    tp_pct = config.get("tp_pct", 0.05)

    entry = position.entry_price
    change = (price - entry) / entry

    if direction == "LONG":
        if change <= -sl_pct:
            return "sl"
        if change >= tp_pct:
            return "tp"
    else:  # SHORT
        if change >= sl_pct:
            return "sl"
        if change <= -tp_pct:
            return "tp"

    return "hold"
