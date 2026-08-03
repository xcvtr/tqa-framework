"""Test strategy — простой MA crossover для проверки backtester'а."""
from __future__ import annotations

from tqa_framework.engine.exchange_base import Signal


def detect(bars: list[dict], config: dict) -> list[Signal]:
    """MA crossover: LONG когда fast > slow, SHORT когда fast < slow."""
    if len(bars) < 30:
        return []

    fast_period = config.get("fast_ma", 5)
    slow_period = config.get("slow_ma", 20)

    closes = [b["close"] for b in bars]

    if len(closes) < slow_period:
        return []

    fast_ma = sum(closes[-fast_period:]) / fast_period
    slow_ma = sum(closes[-slow_period:]) / slow_period

    signals = []
    symbol = config.get("symbol", "TEST")

    # Быстрая над медленной → LONG
    if fast_ma > slow_ma * 1.002:
        signals.append(Signal(
            symbol=symbol,
            direction="LONG",
            price=bars[-1]["close"],
            timestamp=bars[-1]["ts"],
            strategy="test_ma",
            score=1.0,
        ))
    # Быстрая под медленной → SHORT
    elif fast_ma < slow_ma * 0.998:
        signals.append(Signal(
            symbol=symbol,
            direction="SHORT",
            price=bars[-1]["close"],
            timestamp=bars[-1]["ts"],
            strategy="test_ma",
            score=1.0,
        ))

    return signals
