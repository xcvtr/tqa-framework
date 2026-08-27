"""Tests for strategy_engine.actions — action registry and built-in actions."""

from __future__ import annotations

import pytest
from tqa_framework.strategy_engine import actions
from tqa_framework.strategy_engine.models import Signal


class TestActionRegistry:

    def test_get_open_long(self):
        fn = actions.get_action("open_long")
        result = fn("BTC", 50000.0, {}, {"signal_id": "s1", "ts": "now"})
        assert len(result) == 1
        assert result[0].direction == "LONG"
        assert result[0].action == "open_long"
        assert result[0].price == 50000.0
        assert result[0].signal_id == "s1"

    def test_get_open_short(self):
        fn = actions.get_action("open_short")
        result = fn("ETH", 3000.0, {}, {"signal_id": "s1"})
        assert result[0].direction == "SHORT"

    def test_close_long(self):
        result = actions.get_action("close_long")("X", 100.0, {}, {"signal_id": "s1"})
        assert result[0].direction == "CLOSE"
        assert result[0].action == "close_long"

    def test_close_short(self):
        result = actions.get_action("close_short")("X", 100.0, {}, {"signal_id": "s1"})
        assert result[0].direction == "CLOSE"
        assert result[0].action == "close_short"

    def test_close_all(self):
        result = actions.get_action("close_all")("X", 100.0, {}, {"signal_id": "s1"})
        assert result[0].direction == "CLOSE"
        assert result[0].action == "close_all"

    def test_noop(self):
        result = actions.get_action("noop")(None, 0.0, {}, {})
        assert result == []

    def test_register_custom(self):
        def my_action(symbol, price, config, state):
            return [Signal(signal_id="custom", action="test", direction="LONG",
                           price=price, timestamp="now")]
        actions.register_action("my_custom", my_action)
        fn = actions.get_action("my_custom")
        signals = fn("X", 100.0, {}, {})
        assert signals[0].action == "test"

    def test_register_duplicate_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            actions.register_action("open_long", lambda *a: [])

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown action"):
            actions.get_action("does_not_exist")

    def test_list_actions(self):
        names = actions.list_actions()
        assert "open_long" in names
        assert "noop" in names