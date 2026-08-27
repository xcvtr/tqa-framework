"""Metric registry — register callable metrics, resolve by name."""

from __future__ import annotations

import statistics
from typing import Callable

# fn signature: fn(bars: list[dict], params: dict, state: dict) -> float
MetricFn = Callable[..., float]

_registry: dict[str, MetricFn] = {}


def register_metric(name: str, fn: MetricFn) -> None:
    """Register a metric function by name."""
    if name in _registry:
        raise ValueError(f"Metric '{name}' already registered")
    _registry[name] = fn


def get_metric(name: str) -> MetricFn:
    """Look up a registered metric by name."""
    if name not in _registry:
        raise KeyError(f"Unknown metric: '{name}'")
    return _registry[name]


def list_metrics() -> list[str]:
    """Return all registered metric names."""
    return sorted(_registry.keys())


# ─── Built-in metrics ────────────────────────────────────────────────────

def _close(bars: list[dict], params: dict, state: dict) -> float:
    return float(bars[-1]["close"])


def _high(bars: list[dict], params: dict, state: dict) -> float:
    return float(bars[-1]["high"])


def _low(bars: list[dict], params: dict, state: dict) -> float:
    return float(bars[-1]["low"])


def _volume(bars: list[dict], params: dict, state: dict) -> float:
    return float(bars[-1].get("volume", 0))


def _price(bars: list[dict], params: dict, state: dict) -> float:
    """Alias for close."""
    return float(bars[-1]["close"])


def _zscore(bars: list[dict], params: dict, state: dict) -> float:
    """Rolling z-score of a field over period."""
    field = params.get("field", "close")
    period = int(params.get("period", 20))
    values = [float(b[field]) for b in bars[-period:] if field in b]
    if len(values) < 2:
        return 0.0
    mu = statistics.mean(values)
    sigma = statistics.stdev(values)
    return (values[-1] - mu) / sigma if sigma > 0 else 0.0


def _sma(bars: list[dict], params: dict, state: dict) -> float:
    """Simple moving average over period."""
    field = params.get("field", "close")
    period = int(params.get("period", 20))
    values = [float(b[field]) for b in bars[-period:] if field in b]
    return statistics.mean(values) if values else 0.0


def _hour(bars: list[dict], params: dict, state: dict) -> float:
    """Bar hour (0-23)."""
    from datetime import datetime

    dt = bars[-1].get("ts") or bars[-1].get("timestamp", "")
    if isinstance(dt, (int, float)):
        return float(datetime.utcfromtimestamp(dt).hour)
    if isinstance(dt, str):
        return float(dt[11:13]) if "T" in dt else 0.0
    return float(getattr(dt, "hour", 0))


def _session(bars: list[dict], params: dict, state: dict) -> float:
    """Trading session as float: asian=1, european=2, american=3."""
    h = int(_hour(bars, params, state))
    if 0 <= h < 9:
        return 1.0
    if 9 <= h < 14:
        return 2.0
    return 3.0


def _pos_num(bars: list[dict], params: dict, state: dict) -> float:
    """Number of open positions from state."""
    return float(len(state.get("positions", [])))


def _bars_held(bars: list[dict], params: dict, state: dict) -> float:
    """Bars held for current position (max across open positions)."""
    positions = state.get("positions", [])
    if not positions:
        return 0.0
    return float(max(p.bars_held for p in positions))


def _basis(bars: list[dict], params: dict, state: dict) -> float:
    """Basis: fut / lot / spot - 1."""
    fut_field = params.get("fut_field", "fut")
    spot_field = params.get("spot_field", "spot")
    lot = float(params.get("lot", 1))
    fut = float(bars[-1].get(fut_field, 0))
    spot = float(bars[-1].get(spot_field, 1))
    return fut / lot / spot - 1.0 if spot != 0 else 0.0


# Register built-in metrics
_builtins = [
    ("close", _close),
    ("high", _high),
    ("low", _low),
    ("volume", _volume),
    ("price", _price),
    ("zscore", _zscore),
    ("sma", _sma),
    ("hour", _hour),
    ("session", _session),
    ("pos_num", _pos_num),
    ("bars_held", _bars_held),
    ("basis", _basis),
]
for _name, _fn in _builtins:
    _registry[_name] = _fn