"""Tests for strategy_engine.runtime — condition evaluation and signal emission."""

from __future__ import annotations

import pytest
from tests.helpers import fake_bars
from tqa_framework.strategy_engine.models import StrategyDef, SignalConfig, ConditionGroup
from tqa_framework.strategy_engine import runtime


def make_strategy(name, signals, metrics=None):
    return StrategyDef(
        name=name,
        metrics=metrics or {},
        signals=signals,
    )


class TestSimpleCondition:

    def test_price_gt(self):
        bars = [{"ts": 1700000000, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price > 100", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {"symbol": "BTC"})
        assert len(signals) == 1
        assert signals[0].action == "open_long"
        assert signals[0].direction == "LONG"

    def test_price_lt_no_match(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price < 30", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {"symbol": "BTC"})
        assert len(signals) == 0

    def test_close_gt(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="close > 40", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {"symbol": "BTC"})
        assert len(signals) == 1

    def test_zscore_gt(self):
        """zscore with 1 bar returns 0.0, so zscore > -5 is true."""
        bars = [{"ts": 1700000000, "close": 100.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="zscore > -5", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {"symbol": "BTC"})
        assert len(signals) == 1

    def test_hour_condition(self):
        bars = [{"ts": 1700000000, "close": 100}]  # 22:13 UTC, hour=22
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="hour > 20", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1

    def test_between_condition(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price between [100, 200]", then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1


class TestAndGroup:

    def test_all_true(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when=ConditionGroup("all", [
                "price > 100",
                "hour > 0",
            ]), then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1

    def test_all_false(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when=ConditionGroup("all", [
                "price > 100",
                "price < 200",
            ]), then="open_long"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 0


class TestOrGroup:

    def test_any_true(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when=ConditionGroup("any", [
                "price < 50",
                "price > 100",
            ]), then="open_short"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1
        assert signals[0].action == "open_short"

    def test_any_false(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when=ConditionGroup("any", [
                "price > 200",
                "price < 30",
            ]), then="open_short"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 0


class TestFirstMatch:

    def test_first_match_higher_priority(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=5, when="price > 100", then="open_long"),
            SignalConfig(id="s2", priority=10, when="price > 100", then="open_short"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1
        # lower priority = checked first
        assert signals[0].signal_id == "s1"

    def test_first_match_skips_non_matching(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=5, when="price > 200", then="open_long"),
            SignalConfig(id="s2", priority=10, when="price > 30", then="open_short"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1
        assert signals[0].signal_id == "s2"

    def test_no_match_empty(self):
        bars = [{"ts": 1700000000, "close": 50.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=5, when="price > 200", then="open_long"),
            SignalConfig(id="s2", priority=10, when="price < 30", then="open_short"),
        ])
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 0


class TestOtherOperators:

    def test_lte(self):
        bars = [{"ts": 1700000000, "close": 100.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price <= 100", then="open_long"),
        ])
        assert len(runtime.evaluate(bars, strat, {})) == 1

    def test_gte(self):
        bars = [{"ts": 1700000000, "close": 100.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price >= 100", then="open_long"),
        ])
        assert len(runtime.evaluate(bars, strat, {})) == 1

    def test_eq(self):
        bars = [{"ts": 1700000000, "close": 100.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price == 100", then="open_long"),
        ])
        assert len(runtime.evaluate(bars, strat, {})) == 1

    def test_neq(self):
        bars = [{"ts": 1700000000, "close": 100.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price != 200", then="open_long"),
        ])
        assert len(runtime.evaluate(bars, strat, {})) == 1

    def test_between_exclusive(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="price between (100, 200)", then="open_long"),
        ])
        assert len(runtime.evaluate(bars, strat, {})) == 1

    def test_formula_metric(self):
        bars = [{"ts": 1700000000, "close": 200.0, "volume": 1000}]
        from tqa_framework.strategy_engine.models import MetricDef

        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="twice > 300", then="open_long"),
        ], metrics={
            "twice": MetricDef(type="computed", params={"formula": "close * 2"}),
        })
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1

    def test_strategy_metric_override(self):
        bars = [{"ts": 1700000000, "close": 150.0, "volume": 1000}]
        from tqa_framework.strategy_engine.models import MetricDef

        strat = make_strategy("t", [
            SignalConfig(id="s1", priority=10, when="zscore > -200", then="open_long"),
        ], metrics={
            "zscore": MetricDef(type="zscore", params={"field": "close", "period": 20}),
        })
        signals = runtime.evaluate(bars, strat, {})
        assert len(signals) == 1