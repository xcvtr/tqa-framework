"""Tests for full YAML strategy cycle — backtester + strategy_engine integration."""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from tqa_framework.engine.backtester import Backtester


# ── Helpers ──────────────────────────────────────────────────────────


def make_bars(n: int = 100, base_price: float = 100.0) -> list[dict]:
    """Генерирует простые бары для тестов (без CH)."""
    bars = []
    price = base_price
    for i in range(n):
        price *= (1.0 + (0.01 if i % 3 == 0 else -0.005))
        bars.append({
            "ts": 1700000000 + i * 60,
            "open": round(price * 0.999, 2),
            "high": round(price * 1.01, 2),
            "low": round(price * 0.99, 2),
            "close": round(price, 2),
            "volume": 1000 + i * 10,
        })
    return bars


def mock_load_m1_from_ch(*args, **kwargs):
    """Mock для load_m1_from_ch — возвращает тестовые бары."""
    return make_bars(200)


def make_mock_pg():
    """Создаёт MagicMock PGState для изоляции от PG."""
    pg = MagicMock()
    pg.ensure_schemas = MagicMock()
    pg.ensure_tables_backtest = MagicMock()
    pg.save_summary = MagicMock(return_value=1)
    pg.save_trades_batch = MagicMock()
    pg.save_equity_points = MagicMock()
    pg.load_strategy_yaml = MagicMock(return_value=None)
    return pg


class MockResponse:
    """Mock HTTP response for requests.get to CH."""
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
    def raise_for_status(self):
        pass
    def json(self):
        return {}


SIMPLE_YAML = """\
strategy: test_yaml_cycle
version: "2.0"
metrics:
  fast_ma:
    type: sma
    params: {field: close, period: 5}
  slow_ma:
    type: sma
    params: {field: close, period: 20}
  ma_diff:
    type: computed
    params:
      formula: fast_ma - slow_ma
signals:
  - id: long
    priority: 10
    when: "ma_diff > 0"
    then: open_long
  - id: short
    priority: 20
    when: "ma_diff < 0"
    then: open_short
  - id: close_long
    priority: 50
    when: "ma_diff < 0"
    then: close_long
  - id: close_short
    priority: 60
    when: "ma_diff > 0"
    then: close_short
"""


# ── 1. Parser: load strategy from string ─────────────────────────────


class TestYamlCycleParser:
    """Парсинг YAML-стратегии — базовая валидация."""

    def test_parse_yaml(self):
        from tqa_framework.strategy_engine.parser import load_strategy_from_string
        strat = load_strategy_from_string(SIMPLE_YAML)
        assert strat.name == "test_yaml_cycle"
        assert len(strat.signals) == 4
        assert strat.signals[0].id == "long"
        assert strat.signals[0].then == "open_long"


# ── 2. Template YAML ────────────────────────────────────────────────


class TestYamlTemplate:
    """Подстановка параметров в YAML."""

    def test_basic_template(self):
        from tqa_framework.strategy_engine.parser import template_yaml, load_strategy_from_string
        yaml_str = """\
strategy: t
params:
  z_entry: "{{z_entry}}"
metrics:
  z:
    type: zscore
    params:
      period: "{{lookback}}"
signals:
  - id: s1
    priority: 10
    when: "z < -{{z_entry}}"
    then: open_long
"""
        templated = template_yaml(yaml_str, {"z_entry": 2.5, "lookback": 40})
        assert "{{z_entry}}" not in templated
        assert "{{lookback}}" not in templated
        assert "2.5" in templated
        assert "40" in templated
        # После подстановки парсится как valid YAML
        strat = load_strategy_from_string(templated)
        assert strat.name == "t"
        # period="40" → строка, т.к. в YAML quoted. Для int периодов шаблон без кавычек.
        assert str(strat.metrics["z"].params["period"]) == "40"


# ── 3. Sweep config ──────────────────────────────────────────────────


YAML_WITH_SWEEP = """\
strategy: sweep_test
version: "2.0"
signals:
  - id: s1
    when: price > 100
    then: open_long
sweep:
  enabled: true
  params:
    z_entry: [1.5, 2.0, 2.5]
    sl_pct:
      range: [0.02, 0.05, 0.01]
  max_combinations: 16
"""


class TestSweepConfig:
    """Sweep config parsing и генерация комбинаций."""

    def test_parse_sweep(self, tmp_path):
        from tqa_framework.strategy_engine.parser import load_sweep_config
        yml = tmp_path / "test.strategy.yaml"
        yml.write_text(YAML_WITH_SWEEP)
        cfg = load_sweep_config(str(yml))
        assert cfg.enabled is True
        assert "z_entry" in cfg.params
        assert "sl_pct" in cfg.params
        assert cfg.params["z_entry"].values == [1.5, 2.0, 2.5]
        assert cfg.params["sl_pct"].range_min == 0.02
        assert cfg.params["sl_pct"].range_max == 0.05
        assert cfg.params["sl_pct"].range_step == 0.01

    def test_sweep_no_sweep_section(self, tmp_path):
        from tqa_framework.strategy_engine.parser import load_sweep_config
        yml = tmp_path / "no_sweep.strategy.yaml"
        yml.write_text("strategy: x\nsignals:\n  - id: s1\n    when: price > 100\n    then: noop")
        cfg = load_sweep_config(str(yml))
        assert cfg.enabled is False

    def test_build_combinations_discrete(self):
        from tqa_framework.strategy_engine.models import SweepConfig, SweepParam
        from tqa_framework.strategy_engine.parser import build_sweep_combinations
        cfg = SweepConfig(
            enabled=True,
            params={
                "z_entry": SweepParam(values=[1.5, 2.0, 2.5]),
            },
        )
        combos = build_sweep_combinations(cfg)
        assert len(combos) == 3
        assert combos[0] == {"z_entry": 1.5}
        assert combos[1] == {"z_entry": 2.0}
        assert combos[2] == {"z_entry": 2.5}

    def test_build_combinations_range(self):
        from tqa_framework.strategy_engine.models import SweepConfig, SweepParam
        from tqa_framework.strategy_engine.parser import build_sweep_combinations
        cfg = SweepConfig(
            enabled=True,
            params={
                "sl_pct": SweepParam(range_min=0.02, range_max=0.04, range_step=0.01),
            },
        )
        combos = build_sweep_combinations(cfg)
        assert len(combos) == 3
        assert combos[0] == {"sl_pct": 0.02}
        assert combos[1] == {"sl_pct": 0.03}
        assert combos[2] == {"sl_pct": 0.04}


# ── 4. Full cycle: YAML strategy → backtest → trades → metrics ───────


class TestYamlCycleBacktest:
    """Полный цикл: YAML стратегия → Backtester → сделки → метрики."""

    @patch("requests.get", return_value=MockResponse(text="2024-01-15 12:00:00"))
    @patch("tqa_framework.engine.backtester.resample_bars", side_effect=lambda bars, tf: bars)
    @patch("tqa_framework.engine.backtester.load_m1_from_ch", side_effect=mock_load_m1_from_ch)
    def test_full_cycle(self, mock_load, mock_resample, mock_req):
        """Backtester с YAML-стратегией должен открыть сделки и вернуть summary."""
        import tempfile, os
        yml = tempfile.NamedTemporaryFile(mode="w", suffix=".strategy.yaml", delete=False)
        yml.write(SIMPLE_YAML)
        yml.close()

        bt = Backtester(
            tickers=[{"symbol": "TEST", "tf": 60, "risk_pct": 2.0}],
            days=7,
            risk_pct=2.0,
            tf_minutes=60,
            strategy_engine_path=yml.name,
            initial_equity=100_000.0,
            pg=make_mock_pg(),
            ch_host="http://mock:8123",
            ch_db="moex",
            save_results=False,
        )
        result = bt.run()

        os.unlink(yml.name)

        s = result["summary"]
        trades = result["trades"]
        equity = result["equity_curve"]

        assert s["strategy"] == "test_yaml_cycle"
        assert s["total_trades"] > 0, "Должны быть открыты сделки"
        assert len(trades) > 0
        assert len(equity) > 0

        eq_values = [p["equity"] for p in equity]
        assert eq_values[0] == 100_000.0
        assert all(isinstance(v, (int, float)) for v in eq_values)

        for key in ("total_return", "mdd", "win_rate", "profit_factor", "calmar_ratio"):
            assert key in s, f"Missing key: {key}"
            assert isinstance(s[key], (int, float))

    @patch("requests.get", return_value=MockResponse(text="2024-01-15 12:00:00"))
    @patch("tqa_framework.engine.backtester.resample_bars", side_effect=lambda bars, tf: bars)
    @patch("tqa_framework.engine.backtester.load_m1_from_ch", side_effect=mock_load_m1_from_ch)
    def test_close_conditions_work(self, mock_load, mock_resample, mock_req):
        """Exit-сигналы (close_long/close_short) должны закрывать позиции."""
        import tempfile, os

        yaml_str = """\
strategy: close_test
version: "2.0"
metrics:
  z:
    type: zscore
    params: {field: close, period: 10}
signals:
  - id: entry_long
    priority: 10
    when: "z < -1.0"
    then: open_long
  - id: entry_short
    priority: 20
    when: "z > 1.0"
    then: open_short
  - id: exit_all
    priority: 50
    when: "z between [-0.2, 0.2]"
    then: close_all
"""
        yml = tempfile.NamedTemporaryFile(mode="w", suffix=".strategy.yaml", delete=False)
        yml.write(yaml_str)
        yml.close()

        bt = Backtester(
            tickers=[{"symbol": "TEST", "tf": 60, "risk_pct": 2.0}],
            days=7,
            risk_pct=2.0,
            tf_minutes=60,
            strategy_engine_path=yml.name,
            initial_equity=100_000.0,
            pg=make_mock_pg(),
            ch_host="http://mock:8123",
            ch_db="moex",
            save_results=False,
        )
        result = bt.run()

        os.unlink(yml.name)

        trades = result["trades"]
        assert len(trades) > 0
        assert result["summary"]["total_trades"] > 0

        eq = result["equity_curve"]
        assert len(eq) > 10
        final_eq = eq[-1]["equity"]
        assert isinstance(final_eq, float)


# ── 5. Grid sweep integration ────────────────────────────────────────


class TestGridRunnerUnit:
    """Unit-тесты grid/runner.py — без CH."""

    @patch("requests.get", return_value=MockResponse(text="2024-01-15 12:00:00"))
    def test_sweep_params_basic(self, mock_req):
        """sweep_python должна вернуть results для каждой комбинации."""
        from tqa_framework.grid.runner import sweep_python

        pg = make_mock_pg()

        with patch("tqa_framework.engine.backtester.load_m1_from_ch", side_effect=mock_load_m1_from_ch):
            with patch("tqa_framework.engine.backtester.resample_bars", side_effect=lambda bars, tf: bars):
                result = sweep_python(
                    pg=pg,
                    tickers=[{"symbol": "TEST", "tf": 60, "risk_pct": 2.0}],
                    days=7,
                    risk_pct=2.0,
                    tf_minutes=60,
                    strategy_name="test_ma",
                    strategy_path="/home/user/projects/tqa-framework/tqa_framework",
                    params_grid={"fast_ma": [5, 10], "slow_ma": [20]},
                    initial_equity=100_000.0,
                    ch_host="http://mock:8123",
                    ch_db="moex",
                )

        assert "best" in result
        assert "results" in result
        assert len(result["results"]) >= 1
        for entry in result["results"]:
            assert "calmar" in entry
            assert "params" in entry

    @patch("requests.get", return_value=MockResponse(text="2024-01-15 12:00:00"))
    def test_sweep_yaml(self, mock_req, tmp_path):
        """sweep_yaml должна работать с YAML стратегией."""
        from tqa_framework.grid.runner import sweep_yaml

        yml = tmp_path / "test_sweep.strategy.yaml"
        yml.write_text("""\
strategy: sweep_yaml_test
version: "2.0"
metrics:
  z:
    type: zscore
    params:
      field: close
      period: "{{lookback}}"
signals:
  - id: entry_long
    priority: 10
    when: "z < -1.0"
    then: open_long
  - id: exit_all
    priority: 50
    when: "z between [-0.2, 0.2]"
    then: close_all
sweep:
  enabled: true
  params:
    lookback: [10, 20]
  max_combinations: 4
""")

        pg = make_mock_pg()

        with patch("tqa_framework.engine.backtester.load_m1_from_ch", side_effect=mock_load_m1_from_ch):
            with patch("tqa_framework.engine.backtester.resample_bars", side_effect=lambda bars, tf: bars):
                result = sweep_yaml(
                    pg=pg,
                    tickers=[{"symbol": "TEST", "tf": 60, "risk_pct": 2.0}],
                    days=7,
                    risk_pct=2.0,
                    tf_minutes=60,
                    yaml_path=str(yml),
                    initial_equity=100_000.0,
                    ch_host="http://mock:8123",
                    ch_db="moex",
                )

        assert "best" in result
        assert "results" in result
        assert len(result["results"]) >= 1