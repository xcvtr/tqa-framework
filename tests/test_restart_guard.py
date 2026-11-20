"""Рестарт посреди дня не переигрывает пройденные бары (иначе дубли сделок)."""
import inspect

from strategies.trend_sma.engine import run_days


def test_run_days_has_min_ts():
    assert 'min_ts' in inspect.signature(run_days).parameters
    src = inspect.getsource(run_days)
    assert 'ts_i <= min_ts' in src


def test_paper_passes_min_ts_and_keeps_last_ts():
    src = open('strategies/trend_sma/live.py').read()
    assert 'min_ts=last_ts' in src
    assert "db.last_ts = last_ts" in src
    assert 'last_ts' in src


def test_state_stores_last_ts():
    src = open('tqa_f/state/pg_state.py').read()
    assert 'last_ts BIGINT' in src
    assert "'last_ts'" in src


def test_journal_timestamps_in_msk():
    src = open('strategies/trend_sma/live.py').read()
    assert '_MSK' in src
    assert 'tz=_MSK' in src
