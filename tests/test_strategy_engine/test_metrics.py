"""Tests for strategy_engine.metrics — metric registry and built-in metrics."""

from __future__ import annotations

import pytest
from tests.helpers import fake_bars
from tqa_framework.strategy_engine import metrics


class TestMetricRegistry:

    def test_get_price(self):
        bars = fake_bars(20, start_price=150.0)
        fn = metrics.get_metric("price")
        assert fn(bars, {}, {}) == float(bars[-1]["close"])

    def test_get_close(self):
        bars = fake_bars(10)
        assert metrics.get_metric("close")(bars, {}, {}) == float(bars[-1]["close"])

    def test_zscore(self):
        bars = fake_bars(50)
        result = metrics.get_metric("zscore")(bars, {}, {})
        assert isinstance(result, float)

    def test_zscore_short(self):
        bars = fake_bars(1)
        assert metrics.get_metric("zscore")(bars, {}, {}) == 0.0

    def test_sma(self):
        bars = fake_bars(30, start_price=100.0)
        val = metrics.get_metric("sma")(bars, {"period": 20}, {})
        assert isinstance(val, float)
        assert val > 0

    def test_sma_short_data(self):
        bars = fake_bars(5)
        val = metrics.get_metric("sma")(bars, {"period": 20}, {})
        assert val > 0  # sma returns mean of available values

    def test_hour(self):
        bars = [{"close": 100, "ts": 1700000000}]  # 2023-11-14 22:13
        h = metrics.get_metric("hour")(bars, {}, {})
        assert 0 <= h <= 23

    def test_session(self):
        bars = [{"close": 100, "ts": 1700000000}]
        s = metrics.get_metric("session")(bars, {}, {})
        assert s in (1.0, 2.0, 3.0)

    def test_pos_num(self):
        fn = metrics.get_metric("pos_num")
        assert fn([], {}, {"positions": []}) == 0.0
        assert fn([], {}, {"positions": [1, 2]}) == 2.0

    def test_volume(self):
        bars = fake_bars(10)
        v = metrics.get_metric("volume")(bars, {}, {})
        assert v > 0

    def test_high(self):
        bars = [{"close": 100, "high": 105.0, "ts": 1700000000}]
        v = metrics.get_metric("high")(bars, {}, {})
        assert v == 105.0

    def test_low(self):
        bars = [{"close": 100, "low": 95.0, "ts": 1700000000}]
        v = metrics.get_metric("low")(bars, {}, {})
        assert v == 95.0

    def test_bars_held(self):
        class _P:
            bars_held = 5
        fn = metrics.get_metric("bars_held")
        assert fn([], {}, {"positions": [_P()]}) == 5.0
        assert fn([], {}, {"positions": []}) == 0.0

    def test_basis(self):
        bars = [{"close": 100, "fut": 101, "spot": 100}]
        v = metrics.get_metric("basis")(bars, {"lot": 1}, {})
        assert v == pytest.approx(0.01, rel=1e-6)

    def test_session_european(self):
        # ts with hour=9
        bars = [{"close": 100, "ts": 1699952400}]
        s = metrics.get_metric("session")(bars, {}, {})
        assert s == 2.0

    def test_session_asian(self):
        # ts with hour=2
        bars = [{"close": 100, "ts": 1699927200}]
        s = metrics.get_metric("session")(bars, {}, {})
        assert s == 1.0

    def test_list_metrics(self):
        names = metrics.list_metrics()
        assert "price" in names
        assert "sma" in names
        assert "zscore" in names

    def test_register_custom(self):
        metrics.register_metric("my_fn", lambda b, p, s: 42.0)
        assert metrics.get_metric("my_fn")([], {}, {}) == 42.0

    def test_register_duplicate_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            metrics.register_metric("price", lambda b, p, s: 0.0)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown metric"):
            metrics.get_metric("does_not_exist")