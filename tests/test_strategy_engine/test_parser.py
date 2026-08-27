"""Tests for strategy_engine.parser — YAML parsing and validation."""

from __future__ import annotations

import pytest
from tqa_framework.strategy_engine.parser import load_strategy, load_strategy_from_string
from tqa_framework.strategy_engine.models import StrategyDef, SignalConfig, ConditionGroup


class TestParseFromString:

    def test_valid_yaml(self):
        yaml_str = """
strategy: test_strat
version: 1
metrics:
  z:
    type: zscore
    params:
      period: 20
  ma:
    type: sma
    params:
      period: 20
signals:
  - id: buy_signal
    priority: 10
    when: zscore > 2.0
    then: open_long
    params:
      quantity: 0.5
"""
        s = load_strategy_from_string(yaml_str)
        assert isinstance(s, StrategyDef)
        assert s.name == "test_strat"
        assert s.version == "1"
        assert "z" in s.metrics
        assert len(s.signals) == 1
        sig = s.signals[0]
        assert sig.id == "buy_signal"
        assert sig.priority == 10
        assert sig.when == "zscore > 2.0"
        assert sig.then == "open_long"
        assert sig.params == {"quantity": 0.5}

    def test_valid_yaml_with_condition_group(self):
        yaml_str = """
strategy: group_test
signals:
  - id: s1
    priority: 5
    when:
      all:
        - price > 100
        - hour > 10
    then: open_long
"""
        s = load_strategy_from_string(yaml_str)
        sig = s.signals[0]
        assert isinstance(sig.when, ConditionGroup)
        assert sig.when.operator == "all"
        assert len(sig.when.conditions) == 2

    def test_missing_strategy_field(self):
        with pytest.raises(ValueError, match="Missing required field"):
            load_strategy_from_string("signals: []")

    def test_missing_signals(self):
        with pytest.raises(ValueError, match="Missing or empty"):
            load_strategy_from_string("strategy: x")

    def test_empty_signals(self):
        with pytest.raises(ValueError, match="Missing or empty"):
            load_strategy_from_string("strategy: x\nsignals: []")

    def test_unknown_metric_type(self):
        yaml_str = """
strategy: bad
metrics:
  x:
    type: nonexistent_func
signals:
  - id: s1
    when: price > 100
    then: noop
"""
        with pytest.raises(ValueError, match="unknown type"):
            load_strategy_from_string(yaml_str)

    def test_unknown_action(self):
        yaml_str = """
strategy: bad
signals:
  - id: s1
    when: price > 100
    then: fly_to_moon
"""
        with pytest.raises(ValueError, match="unknown action"):
            load_strategy_from_string(yaml_str)

    def test_missing_action(self):
        yaml_str = """
strategy: bad
signals:
  - id: s1
    when: price > 100
"""
        with pytest.raises(ValueError, match="missing"):
            load_strategy_from_string(yaml_str)


class TestLoadStrategy:

    def test_load_from_file(self, tmp_path):
        yml = tmp_path / "strat.strategy.yaml"
        yml.write_text("""
strategy: file_test
signals:
  - id: s1
    when: price > 100
    then: noop
""")
        s = load_strategy(str(yml))
        assert s.name == "file_test"
        assert len(s.signals) == 1

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_strategy("/nonexistent/file.yaml")