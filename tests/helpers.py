"""Helpers for strategy_engine tests."""

from __future__ import annotations

import random


def fake_bars(n: int = 100, start_price: float = 100.0, vol: float = 0.02) -> list[dict]:
    """Generate n fake OHLCV bars starting from start_price with given volatility."""
    random.seed(42)
    bars = []
    price = start_price
    ts = 1700000000  # 2023-11-14
    for i in range(n):
        change = price * vol * random.gauss(0, 1)
        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + price * vol * random.random()
        low_p = min(open_p, close_p) - price * vol * random.random()
        bars.append({
            "ts": ts + i * 60,
            "open": round(open_p, 4),
            "high": round(high_p, 4),
            "low": round(low_p, 4),
            "close": round(close_p, 4),
            "volume": int(random.uniform(100, 10000)),
        })
        price = close_p
    return bars