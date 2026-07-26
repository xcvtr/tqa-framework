"""Stop Hunt detect."""
from __future__ import annotations
from engine.exchange_base import Signal


def stop_hunt_detect(bars: list[dict]) -> list[Signal]:
    raise NotImplementedError
