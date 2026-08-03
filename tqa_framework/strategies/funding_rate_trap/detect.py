"""Funding Rate Trap detect (Binance perps)."""
from __future__ import annotations
from tqa_framework.engine.exchange_base import Signal


def fr_trap_detect(bars: list[dict]) -> list[Signal]:
    raise NotImplementedError
