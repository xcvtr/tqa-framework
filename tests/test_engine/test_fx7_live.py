"""Unit-тесты strategies/fx7/live.py — mock-only, БЕЗ MT5 и БЕЗ тяжёлого backtest.

PG: локальный тестовый Postgres (127.0.0.1:5433/tqa, как docker/pg.sh);
схема на каждый прогон уникальная (scratch). Если PG недоступен — skip.
"""
from __future__ import annotations

import json
import random
import string
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from tqa_framework.engine.exchange_base import ExchangeConfig, Signal
from tqa_framework.engine.exchange_mt5_bridge import ExchangeMT5, ensure_mt5_schema
from strategies.fx7 import cluster_state, live
from strategies.fx7.detect_all import GatesContext
from tqa_framework.engine.gates import GateConfig, GateContext

PG = dict(host="127.0.0.1", port=5433, dbname="tqa", user="postgres", password="tqa")
SCHEMA = "fx7_live_" + "".join(random.choices(string.ascii_lowercase, k=6))
PG_FACTORY = lambda: psycopg2.connect(host="127.0.0.1", port=5433, dbname="tqa",  # noqa: E731
                                      user="postgres", password="tqa", connect_timeout=3)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

FX7_CFG = {
    "strategy": "fx7",
    "detect": {},
    "symbols": [
        {"sym": "euraud", "session": [0, 24], "sl_pct": 1.0, "tp_pct": 2.0,
         "hold_days": 20, "zr": 0.005},
        {"sym": "eurusd", "session": [0, 24], "sl_pct": 1.0, "tp_pct": 0.5,
         "hold_days": 20, "zr": 0.005},
        {"sym": "gbpusd", "session": [0, 24], "sl_pct": 2.0, "tp_pct": 2.0,
         "hold_days": 30, "zr": 0.005},
    ],
    "gates": {"enter_dedup_hours": 24, "correlation_groups": {"usd": ["eurusd", "gbpusd"]}},
    "risk": {"max_conc": 8, "base_risk": 0.47, "common_pool": True, "dd_stop_pct": 0.25,
             "sl_pips": 30, "tp_pips": 60, "comission": 0.0001},
}


class FakeBridge:
    """Заглушка MT5-терминала: кассетные цены, журнал вызовов."""

    def __init__(self):
        self.prices = {"EURUSD": 1.10000}
        self.executed: list[dict] = []
        self.closed: list[str] = []

    def get_price(self, symbol: str) -> float:
        return self.prices.get(symbol.upper(), 1.0)

    def execute_signal(self, sig: dict):
        self.executed.append(dict(sig))
        return True, "mock: executed"

    def close_symbol(self, symbol: str):
        self.closed.append(symbol)
        return True, "mock: closed"

    def get_positions(self) -> list[dict]:
        return []

    def get_account(self) -> dict:
        return {"balance": 1300.0, "equity": 1300.0}


# ─── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def pg_conn():
    try:
        c = psycopg2.connect(**PG, connect_timeout=3)
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


@pytest.fixture()
def clean(pg_conn):
    """Чистые scratch-таблицы контура на каждый тест."""
    with pg_conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        pg_conn.commit()
    ensure_mt5_schema(pg_conn, SCHEMA)
    live.ensure_tables(schema=SCHEMA, conn=pg_conn)
    live.seed_state(pg_conn, SCHEMA, equity=1000.0)
    return pg_conn


def make_ex(prices: dict | None = None) -> tuple[ExchangeMT5, FakeBridge]:
    bridge = FakeBridge()
    if prices:
        bridge.prices.update(prices)
    ex = ExchangeMT5(ExchangeConfig(
        name="mt5", testnet=False,
        params={"pg": {**PG, "schema": SCHEMA}, "mt5": {"symbol_suffix": "rfd"}},
    ), bridge=bridge)
    return ex, bridge


def make_ctx(ex=None, cfg=None, now=NOW) -> live.LiveContext:
    cfg = cfg or FX7_CFG
    gctx = GatesContext(
        config=GateConfig.from_dict(cfg),
        ctx=GateContext(now=now, net=(0.0, 0.0), mom3d=0.1),
        raw=cfg,
    )
    return live.LiveContext(
        config=cfg, executor=ex, schema=SCHEMA, pg_factory=PG_FACTORY,
        gates_ctx=gctx, now=now,
    )


def _seed_open_position(pg_conn, *, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=None, price_current=None, entry_time=None,
                        pyramid_added=0, ticket="t1", equity=1000.0, peak=1000.0):
    """Позиция «на MT5» (mt5_account.positions) + зеркало в PG multi_state."""
    price_open = price_open if price_open is not None else entry
    price_current = price_current if price_current is not None else entry
    entry_time = entry_time if entry_time is not None else NOW.isoformat()
    mt5_row = {
        "ticket": ticket, "symbol": sym.upper() + "rfd" if not sym.upper().endswith("rfd") else sym.upper(),
        "type": "buy" if direction == "BUY" else "sell",
        "volume": 0.01, "price_open": price_open, "price_current": price_current,
        "profit": 0.0, "swap": 0.0,
    }
    with pg_conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.mt5_account SET positions=%s::jsonb, positions_count=%s, "
            f"equity=%s, balance=%s, updated_at=NOW() WHERE id=1",
            (json.dumps([mt5_row]), 1, equity, equity),
        )
        pg_conn.commit()
    live.save_state(pg_conn, SCHEMA, equity=equity, peak=peak, balance=equity,
                    positions=[{
                        "sym": sym.lower(), "direction": direction,
                        "entry_price": price_open, "entry_time": entry_time,
                        "lot": 0.01, "sl_pips": 50, "tp_pips": 0,
                        "trail_activation": 1.5, "trail_trail": 0.8,
                        "best_pnl_pct": 0.0, "cluster_level": 0.0,
                        "pyramid_added": pyramid_added, "ticket": ticket,
                    }], pending=[])


# ─── run_detect ──────────────────────────────────────────────────────────────
def test_run_detect_applies_gates_and_writes_enter(clean, monkeypatch):
    """Гейты применены (ENTER dedup блокирует euraud), ENTER пишется в mt5_signals."""
    ex, _b = make_ex(prices={"EURUSD": 1.10, "EURAUD": 1.05})
    ctx = make_ctx(ex)
    # прошлый ENTER по euraud|SELL → gate_enter_dedup заблокирует новую эмиссию
    live.save_state(clean, SCHEMA, equity=1000.0, peak=1000.0, balance=1000.0,
                    positions=[], pending=[],
                    strategy_config={"recent_enters": {"euraud|SELL": "2026-09-11T11:59"}})

    def fake_detect(symbols, now, config=None, ch=None):
        out = []
        if "euraud" in symbols:
            out.append(Signal("euraud", "SHORT", 1.05, "2026-09-11T12:00:00Z",
                              strategy="fx7", reason="cluster=long@1.05"))
        if "eurusd" in symbols:
            out.append(Signal("eurusd", "LONG", 1.10, "2026-09-11T12:00:00Z",
                              strategy="fx7", reason="cluster=long@1.10"))
        return out

    monkeypatch.setattr(live, "detect", fake_detect)
    # оба кластера живы в PG (R3): eurusd@1.10 и euraud@1.05
    cluster_state.save_clusters({
        "1": {"id": "1", "level": 1.10, "type": "long", "status": "active"},
        "2": {"id": "2", "level": 1.05, "type": "long", "status": "active"},
    }, SCHEMA, conn=clean)
    clean.commit()  # save_clusters сам не коммитит (ожидает autocommit-conn)

    res = live.run_detect(ctx)

    assert res["detected"] == 2
    assert [e["sym"] for e in res["entered"]] == ["eurusd"]  # euraud отфильтрован
    assert any(b["sym"] == "euraud" and "dedup" in b["reason"] for b in res["blocked"])
    # write_mt5_signals вызван → ENTER строка в {schema}.mt5_signals
    with clean.cursor() as cur:
        cur.execute(f"SELECT symbol, type, action FROM {SCHEMA}.mt5_signals WHERE type='ENTER'")
        rows = cur.fetchall()
    assert rows == [("EURUSD", "ENTER", "BUY")]
    # dedup-состояние обновлено в strategy_config
    st = live.load_state(clean, SCHEMA)
    assert "eurusd|BUY" in st["strategy_config"]["recent_enters"]


def test_run_detect_cluster_gone_blocks(clean, monkeypatch):
    """Кластер сигнала отсутствует в PG → gate_cluster_alive блокирует (R3)."""
    ex, _b = make_ex(prices={"EURUSD": 1.10})
    ctx = make_ctx(ex)
    monkeypatch.setattr(live, "detect", lambda symbols, now, config=None, ch=None: [
        Signal("eurusd", "LONG", 1.10, "2026-09-11T12:00:00Z",
               strategy="fx7", reason="cluster=long@9.99"),
    ])
    cluster_state.save_clusters({}, SCHEMA, conn=clean)  # PG пуст — кластера нет
    clean.commit()

    res = live.run_detect(ctx)
    assert res["entered"] == []
    assert any("cluster" in b["reason"] for b in res["blocked"])
    with clean.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.mt5_signals")
        assert cur.fetchone()[0] == 0


def test_run_detect_max_conc_blocks(clean, monkeypatch):
    """Превышение max_conc → ENTER не пишется."""
    ex, _b = make_ex(prices={"EURUSD": 1.10, "EURGBP": 0.86})
    ctx = make_ctx(ex, cfg={
        "symbols": [{"sym": "eurusd", "session": [0, 24]},
                    {"sym": "eurgbp", "session": [0, 24]}],
        "gates": {"enter_dedup_hours": 24},
        "risk": {"max_conc": 1, "base_risk": 0.47, "common_pool": True},
    })
    monkeypatch.setattr(live, "detect", lambda symbols, now, config=None, ch=None: [
        Signal(s, "LONG", 1.10 if s == "eurusd" else 0.86, "2026-09-11T12:00:00Z",
               strategy="fx7", reason="cluster=long@1.10")
        for s in symbols
    ])
    cluster_state.save_clusters(
        {"1": {"id": "1", "level": 1.10, "type": "long", "status": "active"}},
        SCHEMA, conn=clean)
    clean.commit()
    res = live.run_detect(ctx)
    assert len(res["entered"]) == 1  # после первого ENTER — слоты заняты
    assert any("max_conc" in b["reason"] for b in res["blocked"])


# ─── run_tick ────────────────────────────────────────────────────────────────
def test_run_tick_sl_close(clean):
    ex, bridge = make_ex(prices={"EURUSD": 1.05})  # ниже SL (1.089)
    _seed_open_position(clean, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=1.10, price_current=1.101)
    res = live.run_tick(make_ctx(ex))

    assert res["closed"] == 1
    t = res["trades"][-1]
    assert t["reason"] == "sl" and t["sym"] == "eurusd" and t["direction"] == "BUY"
    assert t["pnl_usd"] < 0
    assert bridge.closed == ["EURUSD"]  # EXIT через executor (close_symbol)
    st = live.load_state(clean, SCHEMA)
    assert st["positions"] == []
    with clean.cursor() as cur:
        cur.execute(f"SELECT reason, symbol, direction FROM {SCHEMA}.multi_closed_trades")
        assert cur.fetchall() == [("sl", "eurusd", "BUY")]


def test_run_tick_tp_close(clean):
    ex, _b = make_ex(prices={"EURUSD": 1.11})  # TP 0.5% → 1.1055 (до трейла 1.5%)
    _seed_open_position(clean, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=1.10, price_current=1.101)
    res = live.run_tick(make_ctx(ex))
    assert res["closed"] == 1
    assert res["trades"][-1]["reason"] == "tp"


def test_run_tick_timeout(clean):
    old = (NOW - timedelta(days=25)).isoformat()  # hold_days=20 → просрочена
    ex, _b = make_ex(prices={"EURUSD": 1.10})      # цена = entry (SL/TP не сраб.)
    _seed_open_position(clean, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=1.10, entry_time=old, ticket="t-old")
    res = live.run_tick(make_ctx(ex))
    assert res["closed"] == 1
    t = res["trades"][-1]
    assert t["reason"] == "timeout" and t["hold_hours"] > 24 * 20


def test_run_tick_sync_preserves_pyramid_and_entry_time(clean):
    """sync_mt5: MT5-цена становится entry, pyramid_added/entry_time не сбрасываются."""
    et = (NOW - timedelta(hours=5)).isoformat()
    ex, _b = make_ex(prices={"EURUSD": 1.10})  # цена == entry, ничего не закрываем
    _seed_open_position(clean, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=1.1050, price_current=1.1060,
                        entry_time=et, pyramid_added=1, ticket="t-py")
    res = live.run_tick(make_ctx(ex))
    assert res["closed"] == 0
    st = live.load_state(clean, SCHEMA)
    p = st["positions"][0]
    assert p["entry_price"] == 1.1050            # MT5 fill приоритетен
    assert p["pyramid_added"] == 1               # не сброшен при sync
    assert p["entry_time"] == et                 # не перезаписан
    assert p["ticket"] == "t-py"


def test_run_tick_dd_stop_closes_all(clean):
    """MTM DD > 25% (пик 1000 vs equity 500) → закрыть всё с reason=dd_stop."""
    ex, _b = make_ex(prices={"EURUSD": 1.10})    # цена == entry, без SL/TP-выхода
    _seed_open_position(clean, sym="eurusd", direction="BUY", entry=1.10,
                        price_open=1.10, equity=500.0, peak=1000.0, ticket="t-dd")
    res = live.run_tick(make_ctx(ex))
    assert res["closed"] == 1
    assert res["trades"][-1]["reason"] == "dd_stop"
    st = live.load_state(clean, SCHEMA)
    assert st["positions"] == []


def test_run_tick_mock_consumes_enter_to_position(clean):
    """Dry-run: ENTER из mt5_signals → позиция в mt5_account → sync в PG state."""
    ex = live.Fx7MockExecutor(
        ExchangeConfig(name="mock", testnet=True,
                       params={"pg": {**PG, "schema": SCHEMA}, "symbol_suffix": "rfd"}),
        pg_factory=PG_FACTORY, schema=SCHEMA)
    ex.prices["EURUSD"] = 1.10
    with PG_FACTORY() as c:
        live.write_mt5_signals(
            [{"sym": "eurusd", "direction": "BUY", "entry_price": 1.10,
              "sl_pips": 30, "tp_pips": 60, "lot": 0.01}],
            conn=c, schema=SCHEMA)
    res = live.run_tick(make_ctx(ex))
    assert res["opened"] == 1
    st = live.load_state(clean, SCHEMA)
    assert len(st["positions"]) == 1
    assert st["positions"][0]["sym"] == "eurusd"
    assert st["positions"][0]["direction"] == "BUY"


# ─── sizing / конфиг ─────────────────────────────────────────────────────────
def test_risk_slot_frac_common_pool():
    """risk 0.47 / max_conc 8 → слот 0.05875% equity."""
    assert abs(live.risk_slot_frac(FX7_CFG) - 0.47 / 100 / 8) < 1e-12


def test_size_lot_min_clamp():
    """Очень маленький риск → минимальный лот 0.01."""
    assert live.size_lot(1000.0, 0.0005875, 30) == 0.01


def test_portfolio_from_config_default():
    assert live.portfolio_from_config({}) == live.DEFAULT_PORTFOLIO
    assert live.portfolio_from_config(
        {"symbols": [{"sym": "euraud"}, {"sym": "eurusd"}]}) == ["euraud", "eurusd"]