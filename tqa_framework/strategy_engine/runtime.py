"""Runtime — iterate bars, evaluate rules, emit signals."""

from __future__ import annotations

import re
from typing import Optional

from tqa_framework.strategy_engine.models import (
    ConditionGroup,
    Signal,
    SignalConfig,
    StrategyDef,
)

# Match: metric_name comparator value
# Supports: 'metric < val', 'metric > val', 'metric between [lo, hi]'
_COND_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s+"
    r"(between|<|>|<=|>=|==|!=)\s+"
    r"(.+)$"
)


def _compute_metric(
    name: str,
    bars: list[dict],
    params: dict,
    state: dict,
    strategy: StrategyDef,
) -> float:
    """Resolve and compute a metric by name.

    Lookup order:
      1. External signal metric (when name starts with 'external.')
      2. Strategy-defined metric (type + params override)
      3. Registered metric (built-in or user-registered)
    """
    from tqa_framework.strategy_engine.metrics import get_metric, list_metrics

    # External signal metric: e.g., "external.zscore" accesses state['current_external_signals']
    if name.startswith("external."):
        field = name.split(".", 1)[1]
        current_signals = state.get("current_external_signals", [])
        if not current_signals:
            return 0.0
        # Return value from the first matching external signal
        for sig in current_signals:
            if field in sig:
                return float(sig[field])
        return 0.0  # Field not present in external signal

    # Strategy metrics may override params for a built-in type
    if name in strategy.metrics:
        md = strategy.metrics[name]
        if md.type == "computed":
            formula = md.params.get("formula", "")
            return _eval_formula(formula, bars, state, strategy)
        # Use the metric type as the registry name, pass md.params as params
        fn = get_metric(md.type)
        return fn(bars, md.params, state)

    # Fallback: direct registry lookup
    if name in list_metrics():
        fn = get_metric(name)
        return fn(bars, params, state)

    raise KeyError(f"Unknown metric: '{name}'")


def _eval_formula(formula: str, bars: list[dict], state: dict, strategy: StrategyDef) -> float:
    """Evaluate an arithmetic formula string.

    Supports: +, -, *, /, **, (), metric names, bar field names, numbers.
    Uses restricted eval with a namespace built from bar fields and metrics.
    """
    ns: dict[str, float] = {}
    last = bars[-1]
    for k in ("open", "high", "low", "close", "volume", "fut", "spot", "lot",
              "step_price", "min_step"):
        v = last.get(k)
        if v is not None:
            ns[k] = float(v)

    # Resolve metric references in the formula
    identifiers = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", formula))
    for ident in identifiers:
        if ident not in ns:
            try:
                ns[ident] = _compute_metric(ident, bars, {}, state, strategy)
            except KeyError:
                pass  # will error at eval if truly unknown

    try:
        return float(eval(formula, {"__builtins__": {}}, ns))
    except Exception as e:
        raise ValueError(f"Formula evaluation failed: '{formula}': {e}")


def _eval_condition(
    condition: ConditionGroup | str,
    bars: list[dict],
    state: dict,
    strategy: StrategyDef,
    default_params: dict,
) -> bool:
    """Evaluate a condition (simple string or ConditionGroup) → True/False."""
    if isinstance(condition, str):
        return _eval_simple(condition, bars, state, strategy, default_params)

    results = [
        _eval_condition(c, bars, state, strategy, default_params)
        for c in condition.conditions
    ]
    if condition.operator == "all":
        return all(results)
    return any(results)


def _eval_simple(
    text: str,
    bars: list[dict],
    state: dict,
    strategy: StrategyDef,
    default_params: dict,
) -> bool:
    """Evaluate a simple condition like 'zscore < -2.0' or 'hour between [10, 18.75]'."""
    m = _COND_RE.match(text)
    if not m:
        raise ValueError(f"Invalid condition syntax: '{text}'")

    metric_name, op, raw_value = m.group(1), m.group(2), m.group(3).strip()

    try:
        metric_val = _compute_metric(metric_name, bars, default_params, state, strategy)
    except KeyError as e:
        raise ValueError(f"Condition '{text}': {e}")

    if op == "between":
        inner = raw_value.strip("[]()")
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) != 2:
            raise ValueError(f"Between requires [low, high], got '{raw_value}'")
        low, high = float(parts[0]), float(parts[1])
        left_inc = raw_value.startswith("[")
        right_inc = raw_value.endswith("]")
        if left_inc and right_inc:
            return low <= metric_val <= high
        elif left_inc:
            return low <= metric_val < high
        elif right_inc:
            return low < metric_val <= high
        return low < metric_val < high
    else:
        cmp_val = float(raw_value)
        if op == "<":
            return metric_val < cmp_val
        elif op == ">":
            return metric_val > cmp_val
        elif op == "<=":
            return metric_val <= cmp_val
        elif op == ">=":
            return metric_val >= cmp_val
        elif op == "==":
            return metric_val == cmp_val
        elif op == "!=":
            return metric_val != cmp_val

    raise ValueError(f"Unknown operator '{op}' in condition '{text}'")


def evaluate(
    bars: list[dict],
    strategy: StrategyDef,
    state: dict,
    external_signals: list[dict] | None = None,
    last_bar_only: bool = False,
) -> list[Signal]:
    """Evaluate a strategy over bar data.

    Iterates bars sequentially; for each bar, checks signals by priority (ascending).
    First matching signal per bar fires its action; remaining signals on that bar skipped.
    State dict is updated in-place after each bar.

    When ``last_bar_only=True``, only processes the LAST bar. This is the primary
    mode when called from backtester's incremental detect/tick — the backtester
    already iterates bar by bar, so looping all bars would be O(n²) waste.
    Metrics still receive the full bar history for lookback (zscore, sma, retrace ...).

    Args:
        bars: List of OHLCV bar dicts with 'ts', 'open', 'high', 'low', 'close', 'volume'
        strategy: Parsed StrategyDef
        state: Mutable state dict (persists across bars, updated in-place)
        external_signals: Optional list of pre-computed external signal dicts.
            Each dict should have at least 'ts' (timestamp), 'symbol', and signal data
            (e.g., 'zscore', 'direction'). These are available in state['external_signals']
            for metric/condition evaluation.
        last_bar_only: If True, only evaluate the last bar (default: False).

    Returns list of all Signal objects produced across all bars.
    """
    all_signals: list[Signal] = []

    # Store external signals in state for metric/condition access
    if external_signals is not None:
        state["external_signals"] = external_signals

    start = len(bars) - 1 if last_bar_only else 0
    for i in range(start, len(bars)):
        current_bars = bars if last_bar_only else bars[: i + 1]
        bar = bars[i]
        ts = bar.get("ts") or bar.get("timestamp", "")
        state["ts"] = ts

        # Filter external signals for current bar timestamp
        if external_signals:
            state["current_external_signals"] = [
                s for s in external_signals
                if s.get("ts") == ts or str(s.get("timestamp", "")) == str(ts)
            ]
        else:
            state["current_external_signals"] = []

        for sig_cfg in strategy.signals:
            eval_params = dict(sig_cfg.params)
            state["signal_id"] = sig_cfg.id
            state["reason"] = f"{sig_cfg.id}: {sig_cfg.when}"
            state["signal_params"] = eval_params

            is_triggered = _eval_condition(
                sig_cfg.when, current_bars, state, strategy, eval_params
            )

            if not is_triggered:
                continue

            # First match — execute action
            price = float(bar.get("close", 0.0))
            symbol = state.get("symbol", "?")

            action_fn = _get_action(sig_cfg.then)
            try:
                signals = action_fn(symbol, price, eval_params, state)
            except Exception as e:
                raise RuntimeError(
                    f"Signal '{sig_cfg.id}' action '{sig_cfg.then}' failed: {e}"
                )

            for sig in signals:
                sig.timestamp = ts
            all_signals.extend(signals)

            break  # first-match per bar

    return all_signals


def _get_action(name: str):
    """Lazy import action registry to avoid circular import."""
    from tqa_framework.strategy_engine.actions import get_action
    return get_action(name)