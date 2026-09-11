"""CLI-роутинг: FX-7 paper-режим + регресс LSR-CROSS (crypto не ломаем).

Без реального MT5 и без тяжёлого backtest: run_detect/run_tick подменяются.
PG — локальный тестовый Postgres (127.0.0.1:5433/tqa); scratch-схема уникальна.
"""
from __future__ import annotations

import random
import string

import psycopg2
import pytest

from tqa_framework.engine import cli

PG_URL = "postgresql://postgres:tqa@localhost:5433/tqa"
SCHEMA = "fx7_cli_" + "".join(random.choices(string.ascii_lowercase, k=6))


@pytest.fixture(scope="module")
def pg_conn():
    try:
        c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tqa",
                             user="postgres", password="tqa", connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test PG unavailable: {e}")
    yield c
    try:
        with c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.commit()
    except Exception:  # noqa: BLE001
        pass
    c.close()


def _paper_args(strategy: str, **extra) -> list[str]:
    base = ["paper", "--strategy", strategy]
    args = list(extra.pop("extra", []))
    for k, v in extra.items():
        flag = f"--{k.replace('_', '-')}"
        if v is True:
            args.append(flag)
        elif v is not False:
            args += [flag, str(v)]
    return base + args


def _patch_fx7_cycles(monkeypatch):
    """Реальные циклы FX-7 в CLI-тестах не гоняем — только роутинг."""
    import strategies.fx7.live as fx7_live

    calls: dict = {}

    def _fake_detect(ctx):
        calls["detect_ctx"] = ctx
        return {"detected": 1, "entered": [{"sym": "eurusd", "direction": "BUY"}],
                "ids": ["i1"], "blocked": []}

    def _fake_tick(ctx):
        calls["tick_ctx"] = ctx
        return {"opened": 0, "closed": 0, "equity": 1000.0,
                "peak": 1000.0, "balance": 1000.0, "positions": 0, "trades": []}

    monkeypatch.setattr(fx7_live, "run_detect", _fake_detect)
    monkeypatch.setattr(fx7_live, "run_tick", _fake_tick)
    return calls


def test_cli_fx7_paper_routes_mock_dry_run(monkeypatch):
    """paper --strategy fx7 --executor mock --dry-run → scratch schema + mock executor."""
    import strategies.fx7.live as fx7_live

    calls = _patch_fx7_cycles(monkeypatch)
    args = cli.build_parser().parse_args(
        _paper_args("fx7", executor="mock", dry_run=True,
                    pg_url=PG_URL, scratch_prefix=SCHEMA, mode="both"))
    cli.cmd_paper(args)

    ctx = calls["detect_ctx"]
    assert ctx.schema == SCHEMA                      # scratch, не fx_top
    assert isinstance(ctx.executor, fx7_live.Fx7MockExecutor)
    assert "tick_ctx" in calls                       # mode=both → detect+tick

    # dry-run без сети: схемы созданы на тестовом PG, live не тронут
    with psycopg2.connect(host="127.0.0.1", port=5433, dbname="tqa",
                          user="postgres", password="tqa") as c:
        with c.cursor() as cur:
            cur.execute(f"SELECT to_regclass('{SCHEMA}.multi_state') IS NOT NULL")
            assert cur.fetchone()[0] is True


def test_cli_fx7_paper_routes_mt5_live(monkeypatch):
    """paper --strategy fx7 --executor mt5 (live) → ExchangeMT5 + schema fx_top."""
    from tqa_framework.engine.exchange_mt5_bridge import ExchangeMT5

    calls = _patch_fx7_cycles(monkeypatch)
    cli.cmd_paper(cli.build_parser().parse_args(
        _paper_args("fx7", executor="mt5", pg_url=PG_URL, mode="detect")))

    ctx = calls["detect_ctx"]
    assert ctx.schema == "fx_top"                    # live-схема
    assert isinstance(ctx.executor, ExchangeMT5)


def test_cli_fx7_dry_run_forces_mock(monkeypatch):
    """--dry-run + --executor mt5 → принудительно mock (live не трогаем)."""
    import strategies.fx7.live as fx7_live

    calls = _patch_fx7_cycles(monkeypatch)
    cli.cmd_paper(cli.build_parser().parse_args(
        _paper_args("fx7", executor="mt5", dry_run=True,
                    pg_url=PG_URL, scratch_prefix=SCHEMA, mode="tick")))
    assert isinstance(calls["tick_ctx"].executor, fx7_live.Fx7MockExecutor)


def test_cli_lsr_cross_paper_not_broken(monkeypatch):
    """Существующий paper-режим LSR-CROSS (crypto) работает как раньше."""
    import tqa_framework.engine.paper as paper

    calls = {"tick": False}
    monkeypatch.setattr(paper, "load_config", lambda p: {
        "symbols": ["EURUSD"], "sym_risk": {"EURUSD": 1.0}, "base_risk": 0.08,
    })
    monkeypatch.setattr(paper, "ensure_scratch", lambda *a, **k: None)
    monkeypatch.setattr(paper, "seed_scratch_state", lambda *a, **k: None)
    monkeypatch.setattr(paper, "run_detect", lambda *a, **k: 0)

    def _fake_tick(*a, **k):
        calls["tick"] = True
        return {"opened": 0, "closed": 0, "equity": 475.0, "peak": 475.0,
                "balance": 475.0, "positions": 0, "trades": []}

    monkeypatch.setattr(paper, "run_tick", _fake_tick)
    monkeypatch.setattr(paper, "load_state", lambda *a, **k: (475.0, 475.0, 475.0, []))

    class _Cur:
        def execute(self, *a, **k):
            pass

        def close(self):
            pass

    class _FakePG:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(paper, "_connect_pg", lambda *a, **k: _FakePG())

    cli.cmd_paper(cli.build_parser().parse_args(
        _paper_args("lsr_cross", dry_run=True, pg_url=PG_URL,
                    scratch_prefix=SCHEMA, mode="both")))
    assert calls["tick"] is True                    # lsr_cross тик отработал


def test_parser_defaults_unchanged():
    """Дефолты paper-режима не изменились (executor=mock), mt5 доступен."""
    args = cli.build_parser().parse_args(["paper", "--strategy", "lsr_cross"])
    assert args.executor == "mock"
    args2 = cli.build_parser().parse_args(["paper", "--strategy", "fx7", "--executor", "mt5"])
    assert args2.executor == "mt5"