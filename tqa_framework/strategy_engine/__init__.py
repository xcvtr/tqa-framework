"""Strategy engine — YAML-declarative trading strategies.

Modules:
  models   — dataclasses: StrategyDef, SignalConfig, ConditionGroup, Signal
  parser   — YAML → validated StrategyDef
  metrics  — registry of callable metric functions
  actions  — registry of callable action functions
  runtime  — bar iterator: evaluate conditions → emit signals
"""

from tqa_framework.strategy_engine.metrics import (
    register_metric,
    get_metric,
    list_metrics,
)
from tqa_framework.strategy_engine.actions import (
    register_action,
    get_action,
    list_actions,
)
from tqa_framework.strategy_engine.models import (
    StrategyDef,
    SignalConfig,
    ConditionGroup,
    MetricDef,
    Signal,
)
from tqa_framework.strategy_engine.parser import load_strategy, load_strategy_from_pg, load_strategy_from_string
from tqa_framework.strategy_engine.runtime import evaluate

__all__ = [
    "register_metric", "get_metric", "list_metrics",
    "register_action", "get_action", "list_actions",
    "StrategyDef", "SignalConfig", "ConditionGroup", "MetricDef", "Signal",
    "load_strategy", "load_strategy_from_pg", "load_strategy_from_string", "evaluate",
]