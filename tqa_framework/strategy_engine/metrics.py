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


def _open(bars: list[dict], params: dict, state: dict) -> float:
    """Bar open price."""
    return float(bars[-1]["open"])


def _retrace(bars: list[dict], params: dict, state: dict) -> float:
    """Retracement от экстремума за N баров: (close - period_min) / (period_max - period_min + eps).

    params: field='close', period=20
    Возвращает 0..1 (0=на минимуме, 1=на максимуме).
    """
    field = params.get("field", "close")
    period = int(params.get("period", 20))
    vals = [float(b[field]) for b in bars[-period:] if field in b]
    if len(vals) < 3:
        return 0.5
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-9:
        return 0.5
    return (vals[-1] - mn) / (mx - mn)


def _change(bars: list[dict], params: dict, state: dict) -> float:
    """Процентное изменение close за period баров: (close_N - close_0) / close_0 * 100.

    params: period=1 (1 бар = (close[-1]-close[-2])/close[-2]*100)
    """
    period = int(params.get("period", 1))
    if len(bars) < period + 1:
        return 0.0
    c0 = float(bars[-(period + 1)]["close"])
    c1 = float(bars[-1]["close"])
    if c0 <= 0:
        return 0.0
    return (c1 - c0) / c0 * 100.0


def _dow(bars: list[dict], params: dict, state: dict) -> float:
    """Day of week 0-6 (0=Mon, 6=Sun). Использует UTC."""
    from datetime import datetime
    dt = bars[-1].get("ts") or bars[-1].get("timestamp", "")
    if isinstance(dt, (int, float)):
        return float(datetime.utcfromtimestamp(dt).weekday())
    if isinstance(dt, str):
        return float(datetime.fromisoformat(dt).weekday()) if "T" in dt else 0.0
    return float(getattr(dt, "weekday", lambda: 0)())


def _minute(bars: list[dict], params: dict, state: dict) -> float:
    """Bar minute 0-59."""
    from datetime import datetime
    dt = bars[-1].get("ts") or bars[-1].get("timestamp", "")
    if isinstance(dt, (int, float)):
        return float(datetime.utcfromtimestamp(dt).minute)
    if isinstance(dt, str):
        return float(dt[14:16]) if "T" in dt else 0.0
    return float(getattr(dt, "minute", 0))


def _lowest(bars: list[dict], params: dict, state: dict) -> float:
    """Минимум поля за N баров. params: field='low', period=20"""
    field = params.get("field", "low")
    period = int(params.get("period", 20))
    vals = [float(b[field]) for b in bars[-period:] if field in b]
    return min(vals) if vals else 0.0


def _highest(bars: list[dict], params: dict, state: dict) -> float:
    """Максимум поля за N баров. params: field='high', period=20"""
    field = params.get("field", "high")
    period = int(params.get("period", 20))
    vals = [float(b[field]) for b in bars[-period:] if field in b]
    return max(vals) if vals else 0.0


def _range_pct(bars: list[dict], params: dict, state: dict) -> float:
    """Диапазон high-low за N баров в %: (max_high-min_low)/close[-1]*100.
    params: period=20
    """
    period = int(params.get("period", 20))
    lows = [float(b.get("low", 0)) for b in bars[-period:] if b.get("low")]
    highs = [float(b.get("high", 0)) for b in bars[-period:] if b.get("high")]
    close = float(bars[-1].get("close", 1))
    if not lows or not highs or close <= 0:
        return 0.0
    return (max(highs) - min(lows)) / close * 100.0


def _median_vol(bars: list[dict], params: dict, state: dict) -> float:
    """Медианный объём за N баров. params: period=20"""
    period = int(params.get("period", 20))
    vals = [float(b.get("volume", 0)) for b in bars[-period:] if b.get("volume", 0) > 0]
    if not vals:
        return 0.0
    return float(statistics.median(vals))


def _pnl_pct(bars: list[dict], params: dict, state: dict) -> float:
    """PnL % от первой позиции в state['positions'].
    Нужна для exit-условий в YAML-стратегиях (SL/TP).
    """
    positions = state.get("positions", [])
    if not positions:
        return 0.0
    pos = positions[0]
    return float(pos.pnl_pct if hasattr(pos, 'pnl_pct') else 0.0)


# Register built-in metrics
_builtins = [
    ("close", _close),
    ("high", _high),
    ("low", _low),
    ("volume", _volume),
    ("price", _price),
    ("open", _open),
    ("minute", _minute),
    ("dow", _dow),
    ("zscore", _zscore),
    ("sma", _sma),
    ("lowest", _lowest),
    ("highest", _highest),
    ("range", _range_pct),
    ("retrace", _retrace),
    ("change", _change),
    ("median", _median_vol),
    ("pnl_pct", _pnl_pct),
    ("hour", _hour),
    ("session", _session),
    ("pos_num", _pos_num),
    ("bars_held", _bars_held),
    ("basis", _basis),
]
for _name, _fn in _builtins:
    _registry[_name] = _fn