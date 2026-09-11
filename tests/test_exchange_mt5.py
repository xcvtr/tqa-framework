"""Tests for ExchangeMT5 (live forex executor) — mock bridge, no real terminal.

PG: локальный тестовый Postgres (127.0.0.1:5433/tqa, как docker/pg.sh).
Если PG недоступен — тесты пропускаются (skip). Реальных сделок нет.
"""

from __future__ import annotations

import json
import random
import string

import psycopg2
import pytest

from tqa_framework.engine.exchange_base import ExchangeConfig, Position, Signal
from tqa_framework.engine.exchange_mt5_bridge import (
    ExchangeMT5, BridgeUnavailable, MT5_SYMBOL_MAP, mt5_sym, reverse_sym,
)
from strategies.fx7.bridge import write_mt5_signals, sync_mt5

PG = dict(host="127.0.0.1", port=5433, dbname="tqa", user="postgres", password="tqa")
SCHEMA = "fx7_test_" + "".join(random.choices(string.ascii_lowercase, k=6))


# ─── mock bridge (без MetaTrader5) ──────────────────────────────────────
class FakeBridge:
    """Заглушка MT5-терминала: кассетные цены/позиции/счёт, журнал вызовов."""

    fail_order = False

    def __init__(self):
        self.prices = {"EURUSD": 1.10000}
        self.executed: list[dict] = []
        self.closed: list[str] = []
        self.live_positions: list[dict] = []
        self.account = {"balance": 1300.0, "equity": 1300.5}

    def get_price(self, symbol: str) -> float:
        return self.prices.get(symbol.upper(), 1.0)

    def execute_signal(self, sig: dict):
        self.executed.append(dict(sig))
        if self.fail_order:
            return False, "mock: order rejected"
        return True, "mock: executed"

    def close_symbol(self, symbol: str):
        self.closed.append(symbol)
        return True, "mock: closed"

    def get_positions(self) -> list[dict]:
        return self.live_positions

    def get_account(self) -> dict:
        return dict(self.account)


class NoTerminalBridge(FakeBridge):
    """Имитация недоступного Wine-терминала → заглушка-через-PG."""

    def execute_signal(self, sig: dict):
        raise BridgeUnavailable("no terminal")

    def close_symbol(self, symbol: str):
        raise BridgeUnavailable("no terminal")

    def get_price(self, symbol: str):
        raise BridgeUnavailable("no terminal")


# ─── fixtures ───────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def pg_conn():
    try:
        c = psycopg2.connect(**PG, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test PG unavailable: {e}")
    yield c
    try:
        cur = c.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.commit()
    except Exception:  # noqa: BLE001
        pass
    c.close()


@pytest.fixture()
def exch(pg_conn):
    """ExchangeMT5 на тестовой схеме с mock-bridge (чистая схема на каждый тест)."""
    bridge = FakeBridge()
    cfg = ExchangeConfig(
        name="mt5", testnet=False,
        params={"pg": {**PG, "schema": SCHEMA},
                "mt5": {"server": "AlfaForexRU-Real", "symbol_suffix": "rfd"}},
    )
    ex = ExchangeMT5(cfg, bridge=bridge)
    ex.ensure_schema()
    cur = pg_conn.cursor()
    cur.execute(f"TRUNCATE {SCHEMA}.mt5_signals, {SCHEMA}.mt5_account, "
                f"{SCHEMA}.mt5_loss_tracker, {SCHEMA}.mt5_trade_log")
    pg_conn.commit()
    ex.ensure_schema()  # пересоздать id=1 в mt5_account
    return ex, bridge


# ─── symbol mapping ─────────────────────────────────────────────────────
class TestSymbolMapping:
    def test_forward_map(self):
        assert mt5_sym("EURUSD") == "EURUSDrfd"
        assert mt5_sym("eurusd") == "EURUSDrfd"
        assert mt5_sym("XAUUSD") == "XAUUSDrfd"

    def test_forward_custom_suffix(self):
        # известные символы из MT5_SYMBOL_MAP всегда дают *rfd (карта авторитетна)
        assert mt5_sym("GBPUSD", suffix="") == "GBPUSDrfd"
        # неизвестные — получают конфигурируемый суффикс
        assert mt5_sym("BTCUSD", suffix="") == "BTCUSD"
        assert mt5_sym("BTCUSD", suffix=".m") == "BTCUSD.m"

    def test_reverse_map(self):
        assert reverse_sym("EURUSDrfd") == "EURUSD"
        assert reverse_sym("USDJPYrfd") == "USDJPY"
        assert reverse_sym("EURUSD") == "EURUSD"  # без суффикса — как есть

    def test_full_map_consistency(self):
        for base, mt5_name in MT5_SYMBOL_MAP.items():
            assert mt5_sym(base) == mt5_name
            assert reverse_sym(mt5_name) == base


# ─── open_position ──────────────────────────────────────────────────────
class TestOpenPosition:
    def test_long_writes_enter_and_calls_bridge(self, exch, pg_conn):
        ex, bridge = exch
        sig = Signal(symbol="EURUSD", direction="LONG", price=1.10000,
                     timestamp="2026-09-11T12:00:00Z", strategy="fx7")
        pos = ex.open_position(sig, quantity=0.01)

        assert pos is not None
        assert pos.symbol == "EURUSD" and pos.direction == "LONG"
        assert pos.quantity == 0.01
        assert pos.entry_time == "2026-09-11T12:00:00Z"
        assert pos.id

        # bridge вызван с ENTER/BUY
        assert len(bridge.executed) == 1
        row = bridge.executed[0]
        assert row["type"] == "ENTER" and row["action"] == "BUY"
        assert row["symbol"] == "EURUSD" and float(row["entry"]) == 1.10

        # строка в PG + помечена processed
        cur = pg_conn.cursor()
        cur.execute(
            f"SELECT type, action, symbol, entry, lot_size, processed_at IS NOT NULL "
            f"FROM {SCHEMA}.mt5_signals WHERE id=%s", (pos.id,))
        r = cur.fetchone()
        assert r is not None
        assert (r[0], r[1], r[2]) == ("ENTER", "BUY", "EURUSD")
        assert float(r[3]) == 1.10 and float(r[4]) == 0.01
        assert r[5] is True  # processed_at установлен

    def test_short_maps_to_sell(self, exch, pg_conn):
        ex, bridge = exch
        sig = Signal(symbol="USDJPY", direction="SHORT", price=151.500,
                     timestamp="2026-09-11T12:00:00Z")
        pos = ex.open_position(sig, quantity=0.01)
        assert pos is not None and pos.direction == "SHORT"
        assert bridge.executed[-1]["action"] == "SELL"
        assert bridge.executed[-1]["symbol"] == "USDJPY"

    def test_lot_rounded_to_0_01(self, exch):
        ex, _bridge = exch
        sig = Signal(symbol="EURUSD", direction="LONG", price=1.10,
                     timestamp="2026-09-11T12:00:00Z")
        pos = ex.open_position(sig, quantity=0.017)
        assert pos.quantity == 0.02

    def test_rejected_order_returns_none(self, exch, pg_conn):
        ex, bridge = exch
        bridge.fail_order = True
        sig = Signal(symbol="EURUSD", direction="LONG", price=1.10,
                     timestamp="2026-09-11T12:00:00Z")
        pos = ex.open_position(sig, quantity=0.01)
        assert pos is None
        # ENTER остался в PG, но НЕ processed (ордер не исполнен)
        sig_id = bridge.executed[-1]["id"]
        cur = pg_conn.cursor()
        cur.execute(f"SELECT processed_at IS NULL FROM {SCHEMA}.mt5_signals WHERE id=%s",
                    (sig_id,))
        assert cur.fetchone()[0] is True

    def test_no_terminal_stub_through_pg(self, exch, pg_conn):
        """Терминал недоступен → ENTER в PG, позиция paper-filled, pending."""
        ex, _ = exch
        ex.bridge = NoTerminalBridge()
        sig = Signal(symbol="EURUSD", direction="LONG", price=1.10000,
                     timestamp="2026-09-11T12:00:00Z")
        pos = ex.open_position(sig, quantity=0.01)
        assert pos is not None
        assert pos.entry_price == 1.10
        cur = pg_conn.cursor()
        cur.execute(f"SELECT processed_at IS NULL FROM {SCHEMA}.mt5_signals WHERE id=%s",
                    (pos.id,))
        assert cur.fetchone()[0] is True  # остаётся pending для Wine-bridge


# ─── close_position ─────────────────────────────────────────────────────
class TestClosePosition:
    def test_writes_exit_and_closes(self, exch, pg_conn):
        ex, bridge = exch
        pos = Position(symbol="EURUSD", direction="LONG", entry_price=1.10,
                       current_price=1.101, quantity=0.01, id="t11")
        ok = ex.close_position(pos)
        assert ok is True
        assert bridge.closed == ["EURUSD"]
        cur = pg_conn.cursor()
        cur.execute(f"SELECT type, action, symbol FROM {SCHEMA}.mt5_signals "
                    f"WHERE type='EXIT' ORDER BY created_at DESC LIMIT 1")
        r = cur.fetchone()
        assert r == ("EXIT", "EXIT", "EURUSD")

    def test_no_terminal_returns_true(self, exch, pg_conn):
        ex, _ = exch
        ex.bridge = NoTerminalBridge()
        pos = Position(symbol="EURUSD", direction="LONG", entry_price=1.10,
                       current_price=1.10, quantity=0.01, id="t1")
        assert ex.close_position(pos) is True
        cur = pg_conn.cursor()
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.mt5_signals WHERE type='EXIT'")
        assert cur.fetchone()[0] == 1  # EXIT-сигнал остался для Wine-bridge

    def test_none_position(self, exch):
        ex, _ = exch
        assert ex.close_position(None) is False


# ─── get_positions / balance ────────────────────────────────────────────
class TestPositionsAndBalance:
    def test_positions_from_account(self, exch, pg_conn):
        ex, _ = exch
        _seed_account(pg_conn, [{
            "ticket": 11, "symbol": "EURUSDrfd", "type": "buy", "volume": 0.01,
            "price_open": 1.1000, "price_current": 1.1010, "profit": 1.0, "swap": -0.1,
        }], equity=1300.5)
        positions = ex.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p.symbol == "EURUSD" and p.direction == "LONG" and p.id == "11"
        assert p.quantity == 0.01
        assert p.entry_price == 1.10 and p.current_price == 1.101 and p.pnl == 1.0

    def test_positions_short_mapping(self, exch, pg_conn):
        ex, _ = exch
        _seed_account(pg_conn, [{
            "ticket": 22, "symbol": "USDJPYrfd", "type": "sell", "volume": 0.02,
            "price_open": 151.50, "price_current": 151.20, "profit": 3.0, "swap": 0.0,
        }], equity=1301.0)
        positions = ex.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "USDJPY" and positions[0].direction == "SHORT"

    def test_positions_fallback_to_bridge(self, exch, pg_conn):
        ex, bridge = exch
        _seed_account(pg_conn, [], equity=1300.0)  # пустой positions
        bridge.live_positions = [{
            "ticket": 33, "symbol": "EURGBPrfd", "type": "buy", "volume": 0.01,
            "price_open": 0.8600, "price_current": 0.8610, "profit": 0.5, "swap": 0.0,
        }]
        positions = ex.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "EURGBP" and positions[0].id == "33"

    def test_positions_empty(self, exch, pg_conn):
        ex, _ = exch
        _seed_account(pg_conn, [], equity=1300.0)
        assert ex.get_positions() == []

    def test_balance_from_equity(self, exch, pg_conn):
        ex, _ = exch
        _seed_account(pg_conn, [], equity=1245.75)
        assert ex.get_account_balance() == 1245.75

    def test_balance_zero_before_seed(self, exch):
        """До seed-строки mt5_account баланс = 0 (не падает)."""
        ex, _ = exch
        # ensure_schema уже вставил id=1 с equity=0
        assert ex.get_account_balance() == 0.0


# ─── get_price ──────────────────────────────────────────────────────────
class TestGetPrice:
    def test_via_bridge(self, exch):
        ex, bridge = exch
        bridge.prices["GBPUSD"] = 1.27000
        assert ex.get_price("GBPUSD") == 1.27000

    def test_no_terminal_fallback_zero(self, exch):
        """Терминал и CH недоступны → 0.0 (fail-soft для цены)."""
        ex, _ = exch
        ex.bridge = NoTerminalBridge()
        ex._mt5_params["ch_host"] = "127.0.0.1"
        ex._mt5_params["ch_port"] = 1  # соединение отбивается мгновенно
        assert ex.get_price("EURUSD") == 0.0


# ─── ensure_schema / misc ───────────────────────────────────────────────
class TestSchema:
    def test_ensure_schema_idempotent(self, exch):
        ex, _ = exch
        ex.ensure_schema()  # повторный вызов не должен падать
        ex.ensure_schema()


# ─── strategies/fx7/bridge.py ───────────────────────────────────────────
class TestFx7BridgeHelper:
    def test_write_mt5_signals(self, pg_conn):
        ids = write_mt5_signals(
            [{"sym": "EURUSD", "direction": "BUY", "entry_price": 1.1000,
              "sl_pips": 30, "tp_pips": 60, "lot": 0.01},
             {"sym": "USDJPY", "direction": "SELL", "entry_price": 151.5,
              "lot_size": 0.02}],
            conn=pg_conn, schema=SCHEMA,
        )
        assert len(ids) == 2 and len(set(ids)) == 2
        cur = pg_conn.cursor()
        cur.execute(f"SELECT symbol, type, action, lot_size FROM {SCHEMA}.mt5_signals "
                    f"WHERE id = ANY(%s)", (ids,))
        rows = {(r[0], r[1], r[2], float(r[3])) for r in cur.fetchall()}
        assert ("EURUSD", "ENTER", "BUY", 0.01) in rows
        assert ("USDJPY", "ENTER", "SELL", 0.02) in rows

    def test_sync_mt5_maps_positions(self):
        state = {
            "equity": 1300.0, "peak": 1300.0, "positions": [], "closed_trades": [],
        }
        out = sync_mt5(state, [{
            "symbol": "EURUSDrfd", "type": "buy", "volume": 0.01,
            "price_open": 1.1000, "price_current": 1.1010,
        }])
        assert out["positions"][0]["sym"] == "EURUSD"
        assert out["positions"][0]["direction"] == "BUY"
        assert out["positions"][0]["entry_price"] == 1.10
        # исходный state не мутирован
        assert state["positions"] == []

    def test_sync_mt5_preserves_pyramid_and_entry_time(self):
        state = {
            "equity": 1300.0, "peak": 1300.0, "positions": [{
                "sym": "EURUSD", "direction": "BUY", "entry_price": 1.1000,
                "entry_time": "2026-09-10T00:00:00Z", "lot": 0.01,
                "pyramid_added": 1, "sl_pips": 30, "trail_activation": 1.5,
                "trail_trail": 0.8, "best_pnl_pct": 0.0, "cluster_level": 0.0,
            }], "closed_trades": [],
        }
        out = sync_mt5(state, [{
            "symbol": "EURUSDrfd", "type": "buy", "volume": 0.01,
            "price_open": 1.1050, "price_current": 1.1060,
        }])
        p = out["positions"][0]
        assert p["entry_time"] == "2026-09-10T00:00:00Z"  # сохранён
        assert p["pyramid_added"] == 1  # не сброшен при sync

    def test_sync_mt5_detects_closed(self):
        state = {
            "equity": 1300.0, "peak": 1300.0, "positions": [{
                "sym": "GBPUSD", "direction": "SELL", "entry_price": 1.2700,
                "entry_time": "2026-09-10T00:00:00Z", "lot": 0.01,
                "pyramid_added": 0, "sl_pips": 50, "trail_activation": 1.5,
                "trail_trail": 0.8, "best_pnl_pct": 0.0, "cluster_level": 0.0,
            }], "closed_trades": [],
        }
        out = sync_mt5(state, [], closed_info={"GBPUSD": (13.0, 1.2650)})
        assert out["positions"] == []
        assert len(out["closed_trades"]) == 1
        ct = out["closed_trades"][0]
        assert ct["sym"] == "GBPUSD" and ct["reason"] == "MT5_SL"
        assert ct["pnl_usd"] == 13.0 and ct["exit"] == 1.2650

    def test_sync_mt5_closed_without_pnl(self):
        state = {
            "equity": 1300.0, "peak": 1300.0, "positions": [{
                "sym": "EURUSD", "direction": "BUY", "entry_price": 1.1000,
                "entry_time": "2026-09-10T00:00:00Z", "lot": 0.01,
                "pyramid_added": 0, "sl_pips": 50, "trail_activation": 1.5,
                "trail_trail": 0.8, "best_pnl_pct": 0.0, "cluster_level": 0.0,
            }], "closed_trades": [],
        }
        out = sync_mt5(state, [])
        ct = out["closed_trades"][0]
        assert ct["pnl_usd"] is None  # нет audit-информации — помечено без PnL


# ─── helpers ────────────────────────────────────────────────────────────
def _seed_account(pg_conn, positions: list[dict], equity: float):
    cur = pg_conn.cursor()
    cur.execute(
        f"UPDATE {SCHEMA}.mt5_account SET positions=%s::jsonb, positions_count=%s, "
        f"equity=%s, balance=%s, updated_at=NOW() WHERE id=1",
        (json.dumps(positions), len(positions), equity, equity),
    )
    pg_conn.commit()
