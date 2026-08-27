"""Dataclasses for strategy definitions and signals."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConditionGroup:
    """AND/OR group of conditions.

    operator: 'all' (AND) | 'any' (OR)
    conditions: list of condition strings ('metric < 0') or nested ConditionGroups
    """
    operator: str  # 'all' | 'any'
    conditions: list = field(default_factory=list)


@dataclass
class MetricDef:
    """Metric definition from YAML."""
    type: str  # 'zscore', 'sma', 'computed', 'close', etc.
    params: dict = field(default_factory=dict)


@dataclass
class SignalConfig:
    """One signal rule from parsed YAML.

    when: ConditionGroup | str — group or simple condition string 'metric > val'
    """
    id: str
    priority: int
    when: ConditionGroup | str
    then: str  # action name
    params: dict = field(default_factory=dict)


@dataclass
class StrategyDef:
    """Full parsed strategy definition."""
    name: str
    version: str = "2.0"
    metrics: dict[str, MetricDef] = field(default_factory=dict)
    signals: list[SignalConfig] = field(default_factory=list)


@dataclass
class Signal:
    """Signal emitted by an action execution.

    Compatible with tqa_framework.engine.exchange_base.Signal in structure.
    """
    signal_id: str  # references the SignalConfig id
    action: str     # action name that was executed
    direction: str  # 'LONG' | 'SHORT' | 'CLOSE'
    price: float
    timestamp: str
    params: dict = field(default_factory=dict)
    reason: str = ""