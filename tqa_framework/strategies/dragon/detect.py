"""Dragon detect — поиск паттернов.

Использует engine.detect.run_detect().
Вызов: engine.detect.run_detect(dragon_detect, symbol, tf, ...)
"""

from __future__ import annotations

from tqa_framework.engine.exchange_base import Signal


def dragon_detect(bars: list[dict]) -> list[Signal]:
    """Поиск Dragon паттернов на ресемпленных барах.

    Args:
        bars: Ресемпленные OHLCV бары

    Returns:
        list[Signal] — найденные сигналы
    """
    raise NotImplementedError
