"""YAML parser — strategy YAML → validated StrategyDef."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tqa_framework.strategy_engine.models import (
    ConditionGroup,
    MetricDef,
    SignalConfig,
    StrategyDef,
)

PRIORITY_RE = re.compile(r"^([a-z_]+)\s*(<|>|<=|>=|==|!=|between)\s*(.+)$")


def _parse_condition(val: str | dict) -> ConditionGroup | str:
    """Parse a condition value from YAML into ConditionGroup or simple string.

    String: 'zscore < -2.0' → kept as string for runtime evaluation
    Dict {all: [...]} or {any: [...]} → ConditionGroup
    """
    if isinstance(val, str):
        return val  # simple condition, evaluated by runtime

    if not isinstance(val, dict):
        raise ValueError(f"Invalid condition format: {val}")

    if len(val) != 1:
        raise ValueError(f"Condition group must have exactly one key (all/any), got {val}")

    op = next(iter(val))
    if op not in ("all", "any"):
        raise ValueError(f"Condition group must use 'all' or 'any', got '{op}'")

    conds = val[op]
    if not isinstance(conds, list):
        raise ValueError(f"Condition group '{op}' value must be a list, got {conds}")

    parsed = [_parse_condition(c) for c in conds]
    return ConditionGroup(operator=op, conditions=parsed)


def load_strategy(path: str) -> StrategyDef:
    """Read and validate a YAML strategy file → StrategyDef."""
    raw = yaml.safe_load(Path(path).read_text())
    return _build_strategy(raw)


def load_strategy_from_string(yaml_str: str) -> StrategyDef:
    """Parse and validate a YAML strategy string → StrategyDef."""
    raw = yaml.safe_load(yaml_str)
    return _build_strategy(raw)


def _build_strategy(raw: object) -> StrategyDef:
    """Validate raw parsed YAML → StrategyDef."""
    # Deferred imports to break circular deps at first-use time
    from tqa_framework.strategy_engine.metrics import list_metrics
    from tqa_framework.strategy_engine.actions import list_actions
    if not isinstance(raw, dict):
        raise ValueError("Strategy YAML must be a mapping at top level")

    # ── Required fields ──────────────────────────────────────────────
    name = raw.get("strategy")
    if not name:
        raise ValueError("Missing required field: 'strategy'")
    signals_raw = raw.get("signals")
    if not signals_raw or not isinstance(signals_raw, list):
        raise ValueError("Missing or empty 'signals' list")

    # ── Metrics ──────────────────────────────────────────────────────
    known_metrics = set(list_metrics())
    metrics: dict[str, MetricDef] = {}
    for m_name, m_def in raw.get("metrics", {}).items():
        _type = m_def.get("type", "")
        if not _type:
            raise ValueError(f"Metric '{m_name}' missing 'type' field")

        # Built-in metrics need no def if they match a registry name directly
        if _type in known_metrics:
            metrics[m_name] = MetricDef(type=_type, params=m_def.get("params", {}))
        elif _type == "computed":
            # Computed metrics — unpack formula from nested params
            inner = m_def.get("params", {})
            metrics[m_name] = MetricDef(type=_type, params=inner)
        else:
            raise ValueError(
                f"Metric '{m_name}': unknown type '{_type}'. "
                f"Known: {', '.join(sorted(known_metrics))}"
            )

    # ── Signals ──────────────────────────────────────────────────────
    known_actions = set(list_actions())
    signals: list[SignalConfig] = []
    seen_ids: set[str] = set()

    for i, sig_raw in enumerate(signals_raw):
        sig_id = sig_raw.get("id", "")
        if not sig_id:
            raise ValueError(f"Signal at index {i} missing 'id'")

        if sig_id in seen_ids:
            raise ValueError(f"Duplicate signal id: '{sig_id}'")
        seen_ids.add(sig_id)

        # Validate action
        action_name = sig_raw.get("then", "")
        if not action_name:
            raise ValueError(f"Signal '{sig_id}' missing 'then' (action name)")
        if action_name not in known_actions:
            raise ValueError(
                f"Signal '{sig_id}': unknown action '{action_name}'. "
                f"Known: {', '.join(sorted(known_actions))}"
            )

        # Parse priority
        priority = sig_raw.get("priority", 10)
        if not isinstance(priority, int):
            raise ValueError(f"Signal '{sig_id}': 'priority' must be int, got {priority}")

        # Parse when conditions
        when_raw = sig_raw.get("when")
        if when_raw is None:
            raise ValueError(f"Signal '{sig_id}' missing 'when'")

        try:
            when = _parse_condition(when_raw)
        except ValueError as e:
            raise ValueError(f"Signal '{sig_id}': {e}")

        signals.append(
            SignalConfig(
                id=sig_id,
                priority=priority,
                when=when,
                then=action_name,
                params=sig_raw.get("params", {}),
            )
        )

    # Sort signals by priority ascending (lower = checked first)
    signals.sort(key=lambda s: s.priority)

    return StrategyDef(
        name=name,
        version=str(raw.get("version", "2.0")),
        metrics=metrics,
        signals=signals,
    )


def load_strategy_from_pg(name: str, pg_yaml: str) -> StrategyDef:
    """Load a strategy from a YAML string retrieved from PG.

    Args:
        name: Strategy name (for error messages)
        pg_yaml: Raw YAML string from PG

    Returns:
        Parsed StrategyDef
    """
    raw = yaml.safe_load(pg_yaml)
    if not isinstance(raw, dict):
        raise ValueError(f"Strategy '{name}' in PG: YAML must be a mapping at top level")
    return _build_strategy(raw)