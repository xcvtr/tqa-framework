"""Тесты engine/gates.py — все 12 предикатов (обобщение live_gates)."""
from __future__ import annotations

from datetime import datetime

from tqa_framework.engine.gates import (
    DEFAULT_GATES,
    GateConfig,
    GateContext,
    apply_gates,
    gate_cluster_alive,
    gate_consecutive_loss,
    gate_correlation,
    gate_dir_cfg,
    gate_enter_dedup,
    gate_last_profit_opposite,
    gate_mom3d,
    gate_net,
    gate_session,
    gate_sma72_sell,
    gate_stale_price,
    is_in_session,
)


def _sig(sym="euraud", direction="BUY", entry=1.0, entry_time="2026-01-01 00:00:00"):
    return {
        "sym": sym,
        "direction": direction,
        "entry_price": entry,
        "entry_time": entry_time,
        "cluster_level": 1.0,
        "cluster_type": "long",
    }


def _cfg(**over):
    base = dict(
        session={"euraud": (0, 24), "eurusd": (8, 20)},
        enter_dedup_hours=24.0,
        stale_price_pct=0.3,
        cl3_n=3,
        mom3d_threshold=0.5,
        correlation_groups={
            "eur": ["euraud", "eurgbp", "eurusd"],
            "usd": ["audusd", "eurusd", "gbpusd", "usdcad", "usdchf"],
        },
        dir_cfg={"euraud": "SELL"},
        sell_trend_filter={"euraud": "sma72"},
    )
    base.update(over)
    return GateConfig(**base)


# ── is_in_session / gate_session ─────────────────────────────────────────────
def test_is_in_session_normal():
    cfg = _cfg()
    assert is_in_session("euraud", datetime(2026, 1, 1, 15, 0), cfg)
    assert not is_in_session("eurusd", datetime(2026, 1, 1, 23, 0), cfg)


def test_is_in_session_wraparound():
    cfg = _cfg(session={"gbpjpy": (22, 6)})  # ночь, wraps midnight
    assert is_in_session("gbpjpy", datetime(2026, 1, 1, 23, 0), cfg)
    assert is_in_session("gbpjpy", datetime(2026, 1, 1, 5, 0), cfg)
    assert not is_in_session("gbpjpy", datetime(2026, 1, 1, 12, 0), cfg)


def test_gate_session():
    cfg = _cfg()
    now = datetime(2026, 1, 1, 23, 0)
    assert gate_session(_sig("eurusd"), GateContext(now=now), cfg) == (True, "out-of-session")
    assert gate_session(_sig("euraud"), GateContext(now=now), cfg)[0] is False


# ── gate_enter_dedup ─────────────────────────────────────────────────────────
def test_gate_enter_dedup():
    cfg = _cfg()
    empty = GateContext()
    assert gate_enter_dedup(_sig(), empty, cfg) == (False, None)
    ctx = GateContext(recent_enters={("euraud", "BUY"): "2026-01-01 00:00:00"})
    blocked, reason = gate_enter_dedup(_sig(), ctx, cfg)
    assert blocked and "dedup 24.0h" in reason


# ── gate_consecutive_loss ────────────────────────────────────────────────────
def test_gate_consecutive_loss():
    cfg = _cfg()
    assert gate_consecutive_loss(_sig(), GateContext(pair_cl={"euraud": 2}), cfg) == (False, None)
    blocked, reason = gate_consecutive_loss(_sig(), GateContext(pair_cl={"euraud": 3}), cfg)
    assert blocked and "3>= 3" in reason


# ── gate_correlation ─────────────────────────────────────────────────────────
def test_gate_correlation():
    cfg = _cfg()
    positions = [
        {"sym": "eurusd", "direction": "BUY"},
        {"sym": "eurgbp", "direction": "BUY"},
    ]
    blocked, reason = gate_correlation(_sig(), GateContext(positions=positions), cfg)
    assert blocked and "eur" in reason
    # разные направления — не блокирует
    positions2 = [
        {"sym": "eurusd", "direction": "BUY"},
        {"sym": "eurgbp", "direction": "SELL"},
    ]
    assert gate_correlation(_sig(), GateContext(positions=positions2), cfg)[0] is False


# ── gate_stale_price ─────────────────────────────────────────────────────────
def test_gate_stale_price():
    cfg = _cfg()
    assert gate_stale_price(_sig(entry=1.0), GateContext(price=1.001), cfg) == (False, None)
    blocked, reason = gate_stale_price(_sig(entry=1.0), GateContext(price=1.004), cfg)
    assert blocked and "stale price" in reason
    # нет цены → no-price
    assert gate_stale_price(_sig(entry=1.0), GateContext(price=None), cfg) == (True, "no-price")


# ── gate_sma72_sell ──────────────────────────────────────────────────────────
def test_gate_sma72_sell():
    cfg = _cfg()
    # SELL в uptrend (price > sma72) при sell_trend_filter=sma72 → блок
    ctx = GateContext(price=1.05, sma72=1.02)
    blocked, reason = gate_sma72_sell(_sig(direction="SELL"), ctx, cfg)
    assert blocked and "SMA72" in reason
    # SELL ниже sma72 → ок
    ctx2 = GateContext(price=1.01, sma72=1.02)
    assert gate_sma72_sell(_sig(direction="SELL"), ctx2, cfg)[0] is False
    # BUY не фильтруется
    assert gate_sma72_sell(_sig(direction="BUY"), ctx, cfg)[0] is False


# ── gate_dir_cfg ─────────────────────────────────────────────────────────────
def test_gate_dir_cfg():
    cfg = _cfg(dir_cfg={"euraud": "SELL"})
    assert gate_dir_cfg(_sig(direction="SELL"), GateContext(), cfg) == (False, None)
    blocked, reason = gate_dir_cfg(_sig(direction="BUY"), GateContext(), cfg)
    assert blocked and "SELL-only" in reason


# ── gate_mom3d ───────────────────────────────────────────────────────────────
def test_gate_mom3d():
    cfg = _cfg(mom3d_threshold=0.5)
    assert gate_mom3d(_sig(), GateContext(mom3d=0.2), cfg) == (False, None)
    blocked, reason = gate_mom3d(_sig(), GateContext(mom3d=0.6), cfg)
    assert blocked and "mom3d" in reason
    assert gate_mom3d(_sig(), GateContext(mom3d=None), cfg)[0] is False


# ── gate_net ─────────────────────────────────────────────────────────────────
def test_gate_net():
    cfg = _cfg()
    # толпа сбрасывает (net_now < net_prev*1.2) → ок
    assert gate_net(_sig(), GateContext(net=(100.0, 200.0)), cfg)[0] is False
    # толпа набирает → блок
    blocked, reason = gate_net(_sig(), GateContext(net=(300.0, 200.0)), cfg)
    assert blocked and "NET" in reason
    # fail-closed: net недоступен → блок (R10)
    blocked, _ = gate_net(_sig(), GateContext(net=None), cfg)
    assert blocked


# ── gate_cluster_alive ───────────────────────────────────────────────────────
def test_gate_cluster_alive():
    cfg = _cfg()
    assert gate_cluster_alive(_sig(), GateContext(cluster_alive=True), cfg) == (False, None)
    assert gate_cluster_alive(_sig(), GateContext(cluster_alive=False), cfg) == (True, "cluster gone")


# ── gate_last_profit_opposite ────────────────────────────────────────────────
def test_gate_last_profit_opposite():
    cfg = _cfg()
    # последняя прибыльная — противоположное направление → блок
    ctx = GateContext(last_trade=("SELL", 50.0))
    blocked, reason = gate_last_profit_opposite(_sig(direction="BUY"), ctx, cfg)
    assert blocked and "против" in reason
    # прибыльная, но то же направление → ок
    assert gate_last_profit_opposite(_sig(direction="SELL"), ctx, cfg)[0] is False
    # убыточная последняя → не блокирует
    assert gate_last_profit_opposite(
        _sig(direction="BUY"), GateContext(last_trade=("SELL", -10.0)), cfg)[0] is False


# ── apply_gates / DEFAULT_GATES ──────────────────────────────────────────────
def test_default_gates_count():
    # 11 gate_* + is_in_session helper = 12 предикатов
    assert len(DEFAULT_GATES) == 11
    assert callable(is_in_session)


def test_apply_gates_passes_clean_signal():
    cfg = _cfg(dir_cfg={})
    ctx = GateContext(now=datetime(2026, 1, 1, 12, 0), price=1.0, cluster_alive=True,
                      net=(100.0, 200.0), mom3d=0.1)
    blocked, reason = apply_gates(_sig(), ctx, cfg)
    assert not blocked
    assert reason is None


def test_apply_gates_first_block():
    cfg = _cfg(dir_cfg={})
    ctx = GateContext(now=datetime(2026, 1, 1, 12, 0), price=1.0, cluster_alive=True,
                      net=(100.0, 200.0), pair_cl={"euraud": 3})
    blocked, reason = apply_gates(_sig(), ctx, cfg)
    assert blocked and reason.startswith("ConsecutiveLoss")


# ── конфиг из YAML/dict ─────────────────────────────────────────────────────
def test_gate_config_from_dict():
    data = {
        "gates": {
            "enter_dedup_hours": 24,
            "stale_price_pct": 0.3,
            "cl3_n": 3,
            "mom3d_threshold": 0.5,
            "correlation_groups": {"usd": ["eurusd", "usdchf"]},
            "dir_cfg": {"usdchf": "SELL"},
            "net_growth_factor": 1.2,
        },
        "symbols": [
            {"sym": "eurusd", "session": [0, 24], "sell_trend_filter": "sma72"},
            {"sym": "usdchf", "session": [0, 24], "sell_trend_filter": "sma72"},
        ],
    }
    cfg = GateConfig.from_dict(data)
    assert cfg.enter_dedup_hours == 24.0
    assert cfg.correlation_groups["usd"] == ["eurusd", "usdchf"]
    assert cfg.dir_cfg["usdchf"] == "SELL"
    assert cfg.session_for("eurusd") == (0, 24)
    assert cfg.sell_trend_filter["usdchf"] == "sma72"
    # отсутствующий символ → 24/7
    assert cfg.session_for("xauusd") == (0, 24)
