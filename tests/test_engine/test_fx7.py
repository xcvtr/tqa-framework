"""Smoke-тесты FX-7 проекта: detect, detect_all (rolling), cluster_state (PG)."""
from __future__ import annotations

from datetime import datetime

import pytest

from tqa_framework.engine.exchange_base import Signal

from strategies.fx7 import cluster_state
from strategies.fx7.detect import detect, to_signal
from strategies.fx7.detect_all import (
    GatesContext,
    fx7_detect_all,
    fx7_detect_all_rolling,
)

# ── helpers ──────────────────────────────────────────────────────────────────
def _cand(sym="euraud", direction="SELL", entry=1.05, entry_time="2026-01-01 00:00:00"):
    return {
        "sym": sym, "direction": direction,
        "entry_price": entry, "exit_price": entry,
        "entry_time": entry_time, "exit_time": entry_time,
        "pnl_pips": 0.0, "cluster_type": "long", "cluster_level": 1.05,
    }


class _FakeLiveEngine:
    def __init__(self, cands):
        self._cands = cands
        self.calls = 0

    def generate_candidates(self, symbols, now, **kw):
        self.calls += 1
        return [dict(c) for c in self._cands]


def _gates_ctx():
    from tqa_framework.engine.gates import GateConfig, GateContext

    cfg = GateConfig(
        session={"euraud": (0, 24)},
        enter_dedup_hours=24.0, stale_price_pct=0.3, cl3_n=3,
        mom3d_threshold=0.5,
        correlation_groups={"eur": ["euraud", "eurusd"]},
        dir_cfg={}, sell_trend_filter={}, net_growth_factor=1.2,
    )
    return GatesContext(
        config=cfg,
        ctx=GateContext(
            now=datetime(2026, 1, 1, 12, 0),
            price=1.05, net=(100.0, 200.0), mom3d=0.1, cluster_alive=True,
        ),
    )


# ── detect ───────────────────────────────────────────────────────────────────
def test_to_signal_maps_direction():
    s = to_signal(_cand(direction="SELL"))
    assert isinstance(s, Signal)
    assert s.symbol == "euraud"
    assert s.direction == "SHORT"      # SELL → SHORT (framework)
    assert s.price == 1.05
    assert s.timestamp == "2026-01-01 00:00:00"
    assert s.strategy == "fx7"


def test_detect_wraps_generate_candidates(monkeypatch):
    fake = _FakeLiveEngine([_cand(), _cand("eurusd", "BUY", 1.10)])
    import strategies.fx7.detect as d

    monkeypatch.setattr(d, "_live_engine", lambda: fake)
    now = datetime(2026, 1, 1, 12, 0)
    sigs = detect(["euraud", "eurusd"], now, config={"detect": {"threshold": 1.5}})
    assert len(sigs) == 2
    assert {s.direction for s in sigs} == {"SHORT", "LONG"}
    assert fake.calls == 1


# ── detect_all ───────────────────────────────────────────────────────────────
def test_fx7_detect_all_applies_gates(monkeypatch):
    ctx = _gates_ctx()
    ctx.ctx.pair_cl = {"euraud": 3}   # три подряд убытка → CL3 блокирует
    import strategies.fx7.detect_all as da

    monkeypatch.setattr(da, "detect", lambda symbols, now, config=None, ch=None: [
        to_signal(_cand("euraud", "SELL", 1.05)),
        to_signal(_cand("eurusd", "BUY", 1.05)),
    ])
    passed = fx7_detect_all(["euraud", "eurusd"], datetime(2026, 1, 1, 12, 0), ctx)
    # euraud заблокирован CL3, eurusd прошёл
    assert [s.symbol for s in passed] == ["eurusd"]


def test_fx7_detect_all_rolling_dedup(monkeypatch):
    ctx = _gates_ctx()
    import strategies.fx7.detect_all as da

    def fake_detect(symbols, now, config=None, ch=None):
        # одна и та же пара каждый шаг
        return [to_signal(_cand("euraud", "SELL", 1.05, entry_time=now.strftime("%Y-%m-%d %H:%M:%S")))]

    monkeypatch.setattr(da, "detect", fake_detect)
    start = datetime(2026, 1, 1, 0, 0)
    end = datetime(2026, 1, 1, 2, 0)
    entries = fx7_detect_all_rolling(["euraud"], start, end, ctx, step_minutes=20)
    # ENTER-24h dedup: на 20-мин цикле одна пара войдёт лишь один раз
    assert len(entries) == 1


def test_gates_ctx_from_yaml(tmp_path):
    import yaml

    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump({
        "gates": {"enter_dedup_hours": 24, "correlation_groups": {"usd": ["eurusd"]}},
        "symbols": [{"sym": "eurusd", "session": [0, 24]}],
    }))
    gctx = GatesContext.from_yaml(str(p), now=datetime(2026, 1, 1, 12, 0))
    assert gctx.config.enter_dedup_hours == 24.0
    assert gctx.config.correlation_groups["usd"] == ["eurusd"]


# ── cluster_state (тестовый PG: localhost:5433/tqa) ─────────────────────────
@pytest.fixture
def pg_schema():
    return "fx7_test"


def test_cluster_state_roundtrip(pg_schema):
    c = cluster_state._conn()
    # подчистить схему на случай повтора
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {pg_schema} CASCADE")
    cluster_state.ensure_multi_state_clusters(pg_schema, conn=c)
    clusters = cluster_state.load_clusters(pg_schema, conn=c)
    assert clusters == {}

    cluster_state.save_clusters({"1": {"id": "1", "level": 1.05, "type": "long", "status": "active"}},
                                pg_schema, conn=c)
    loaded = cluster_state.load_clusters(pg_schema, conn=c)
    assert loaded["1"]["level"] == 1.05


def test_cluster_state_update(pg_schema):
    c = cluster_state._conn()
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {pg_schema} CASCADE")
    res = cluster_state.update("2026-01-01 00:00:00", [(1.05, "long", 10.0)], zr=0.005, schema=pg_schema, conn=c)
    assert len(res) == 1
    cid = list(res)[0]
    assert res[cid]["status"] == "active"
    assert res[cid]["type"] == "long"
    # тот же центроид — матч, а не новый
    res2 = cluster_state.update("2026-01-01 00:20:00", [(1.05, "long", 12.0)], zr=0.005, schema=pg_schema, conn=c)
    assert len(res2) == 1
    assert res2[cid]["peak_volume"] == 12.0


def test_cluster_state_migrate_json(pg_schema, tmp_path):
    import json

    src = {"clusters": {"10": {"id": "10", "level": 1.2, "type": "short", "status": "active"}}}
    p = tmp_path / "clusters.json"
    p.write_text(json.dumps(src))
    c = cluster_state._conn()
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {pg_schema} CASCADE")
    n = cluster_state.migrate_from_json(str(p), schema=pg_schema, conn=c)
    assert n == 1
    loaded = cluster_state.load_clusters(pg_schema, conn=c)
    assert loaded["10"]["level"] == 1.2
