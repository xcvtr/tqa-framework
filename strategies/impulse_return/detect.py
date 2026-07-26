"""Impulse Return detect."""
from __future__ import annotations
from engine.exchange_base import Signal


def impulse_return_detect(bars: list[dict]) -> list[Signal]:
    raise NotImplementedError
