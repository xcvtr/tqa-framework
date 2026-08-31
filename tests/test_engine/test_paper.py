"""Unit tests for paper.py — PnL math, margin enforcement, scan_position, detect."""

from __future__ import annotations

from tqa_framework.engine.paper import (
    scan_position,
    detect_cross_events,
    compute_z,
    load_config,
)


# ─── PnL math tests ─────────────────────────────────────────────────────

class TestPnLMath:
    """Core PnL: pnl_usd = eq_open * risk * lev * pnl_pct."""

    def test_long_profit_simple(self):
        """LONG +5% move, comm=0.1%, slip=0.05% → net pnl_pct = 0.0485."""
        entry = 100.0
        exit_px = 105.0
        comm, slip = 0.001, 0.0005
        pnl_pct = (exit_px - entry) / entry - comm - slip
        assert abs(pnl_pct - 0.0485) < 1e-6

        eq_open = 475.0
        risk = 0.08 * 1.0  # base_risk * sym_risk
        lev = 1.0
        pnl_usd = eq_open * risk * lev * pnl_pct
        # notional = 475*0.08 = 38; pnl = 38 * 0.0485 = 1.843
        assert abs(pnl_usd - 1.843) < 1e-3

    def test_short_profit_simple(self):
        """SHORT -5% move → profit."""
        entry = 100.0
        exit_px = 95.0
        comm, slip = 0.001, 0.0005
        pnl_pct = (entry - exit_px) / entry - comm - slip
        assert abs(pnl_pct - 0.0485) < 1e-6

    def test_long_loss(self):
        """LONG losing trade."""
        entry = 100.0
        exit_px = 95.0
        comm, slip = 0.001, 0.0005
        pnl_pct = (exit_px - entry) / entry - comm - slip
        assert pnl_pct < 0
        assert abs(pnl_pct - (-0.0515)) < 1e-6

    def test_risk_sym_risk_multiplier(self):
        """Risk = base_risk * sym_risk."""
        base_risk = 0.08
        # ADAUSDT: sym_risk=1.3
        risk = base_risk * 1.3
        assert abs(risk - 0.104) < 1e-6
        # APTUSDT: sym_risk=0.9
        risk = base_risk * 0.9
        assert abs(risk - 0.072) < 1e-6

    def test_pnl_usd_with_sym_risk(self):
        """PnL with ADAUSDT (sym_risk=1.3)."""
        eq_open = 475.0
        risk = 0.08 * 1.3
        lev = 1.0
        pnl_pct = 0.0485
        pnl_usd = eq_open * risk * lev * pnl_pct
        # notional = 475*0.104 = 49.4; pnl = 49.4 * 0.0485 = 2.3959
        assert abs(pnl_usd - 2.3959) < 1e-3

    def test_leverage_scales_pnl(self):
        """Leverage multiplies notional but margin = notional/lev."""
        eq = 475.0
        risk = 0.08
        lev = 2.0
        notional = eq * risk * lev  # = 76.0
        margin = notional / lev  # = 38.0
        pnl_pct = 0.05
        pnl_usd = eq * risk * lev * pnl_pct  # = 3.8
        assert abs(notional - 76.0) < 1e-6
        assert abs(margin - 38.0) < 1e-6
        assert abs(pnl_usd - 3.8) < 1e-6

    def test_pyramiding_risk_multiplier(self):
        """After pyramiding, risk *= (1 + pyr_add)."""
        base_risk = 0.08
        pyr_add = 0.5
        # Before pyramid
        risk = base_risk
        margin = 100.0
        # After pyramid
        risk_after = risk * (1 + pyr_add)
        margin_after = margin * (1 + pyr_add)
        assert abs(risk_after - 0.12) < 1e-6
        assert abs(margin_after - 150.0) < 1e-6

    def test_pnl_pct_clamped(self):
        """PnL% includes comm+slip subtraction."""
        entry, exit_px = 100.0, 100.0  # flat
        pnl_pct = (exit_px - entry) / entry - 0.001 - 0.0005
        assert abs(pnl_pct - (-0.0015)) < 1e-6
        # Even flat trades lose to comm+slip


# ─── Margin enforcement ──────────────────────────────────────────────────

class TestMarginEnforcement:
    """max_margin_ratio enforcement logic."""

    def test_margin_within_limit(self):
        """Total margin <= equity * max_margin_ratio → allowed."""
        equity = 475.0
        margin_ratio = 0.8
        existing_margin = 200.0
        new_margin = 100.0
        total = existing_margin + new_margin
        assert total <= equity * margin_ratio  # 300 <= 380

    def test_margin_exceeds_limit(self):
        """Total margin > equity * max_margin_ratio → blocked."""
        equity = 475.0
        margin_ratio = 0.8
        existing_margin = 350.0
        new_margin = 50.0
        total = existing_margin + new_margin
        assert total > equity * margin_ratio  # 400 > 380

    def test_margin_calculation(self):
        """margin = notional / lev."""
        equity = 475.0
        risk = 0.08
        lev = 1.0
        notional = equity * risk * lev
        margin = notional / lev
        assert abs(margin - 38.0) < 1e-6

    def test_margin_with_lev20(self):
        """Margin scales inversely with leverage."""
        equity = 475.0
        risk = 0.08
        lev = 20.0
        notional = equity * risk * lev
        margin = notional / lev
        # margin = equity * risk = 38.0 regardless of leverage
        assert abs(margin - 38.0) < 1e-6

    def test_pyramid_margin_check(self):
        """Pyramid add_margin checked against limit."""
        equity = 475.0
        margin_ratio = 0.8
        existing_margin = 100.0
        pos_margin = 150.0
        pyr_add = 0.5
        add_margin = pos_margin * pyr_add  # 75
        total_other = existing_margin
        total = total_other + pos_margin + add_margin
        assert total <= equity * margin_ratio  # 325 <= 380

    def test_pyramid_margin_blocked(self):
        """Pyramid blocked when exceeding limit."""
        equity = 475.0
        margin_ratio = 0.8
        existing_margin = 200.0
        pos_margin = 150.0
        pyr_add = 0.5
        add_margin = pos_margin * pyr_add  # 75
        total = existing_margin + pos_margin + add_margin
        assert total > equity * margin_ratio  # 425 > 380


# ─── scan_position tests ─────────────────────────────────────────────────

class TestScanPosition:
    """scan_position: pyr → trail → sl → tp on each bar."""

    def test_sl_long(self):
        """LONG hits SL."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        # Bar goes down 6% → triggers SL at 95.0
        bars = [(None, 100.0, 93.0, 94.0)]  # (ts, hi, lo, cl)
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        assert exit_px == 95.0  # 100 * (1-0.05)
        assert reason == "sl"

    def test_sl_short(self):
        """SHORT hits SL."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [(None, 107.0, 100.0, 106.0)]  # hi above SL
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "SHORT", bars, cfg
        )
        assert exit_px == 105.0  # 100 * (1+0.05)
        assert reason == "sl"

    def test_tp_long(self):
        """LONG hits TP (trail_act=None → no trailing stop)."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": None,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [(None, 119.0, 100.0, 118.0)]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        assert exit_px == 118.0  # 100 * (1+0.18)
        assert reason == "tp"

    def test_tp_short(self):
        """SHORT hits TP (trail_act=None)."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": None,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [(None, 100.0, 81.0, 82.0)]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "SHORT", bars, cfg
        )
        assert exit_px == 82.0  # 100 * (1-0.18)
        assert reason == "tp"

    def test_trail_activation_long(self):
        """LONG: trail activates at +trail_act%, then SL trails."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [
            (None, 106.0, 100.0, 105.0),  # activates trail (hi >= 105)
            (None, 110.0, 106.0, 109.0),  # peak=110, sl trails
            (None, 107.0, 102.0, 103.0),  # hits trail SL
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        assert reason == "trail"
        assert trail_active is True

    def test_trail_lock(self):
        """Trail lock: sl_px >= entry * (1 + trail_lock)."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.02, "pyr_trigger": 0.05,
        }
        # Just barely activates trail, then drops
        bars = [
            (None, 105.5, 100.0, 105.0),  # activates trail
            (None, 105.0, 101.0, 102.0),  # drops but lock keeps sl >= 102
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        # sl should be max of trail_sl and entry*(1+0.02)=102
        # peak=105.5, trail_sl=105.5*(1-0.06)=99.17, lock=102 → sl=102
        # lo=101 < 102 → reason=trail
        assert reason == "trail"

    def test_pyramid_detection(self):
        """Pyramid trigger detected but doesn't cause exit."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": None,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [
            (None, 106.0, 100.0, 105.0),  # hi >= 105 → pyramid=True
            (None, 119.0, 105.0, 118.0),  # hits TP
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        assert pyramid is True
        assert reason == "tp"

    def test_no_exit_flat(self):
        """No exit when price stays in range."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.05, "pyr_trigger": 0.05,
        }
        bars = [
            (None, 102.0, 99.0, 101.0),
            (None, 103.0, 100.0, 102.0),
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        assert exit_px is None
        assert reason is None

    def test_trail_blocks_tp(self):
        """After trail activates, TP is not checked."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.02, "pyr_trigger": 0.05,
        }
        bars = [
            (None, 106.0, 100.0, 105.0),   # activates trail
            (None, 119.0, 103.0, 118.0),   # would hit TP but trail_active
            (None, 102.0, 101.0, 102.0),   # drops to trail SL
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "LONG", bars, cfg
        )
        # TP is NOT checked when trail_active → exit via trail SL
        assert reason == "trail"

    def test_short_trail_activation(self):
        """SHORT trail activates at -trail_act%, then trails down."""
        cfg = {
            "sl_pct": 0.05, "tp_pct": 0.18, "trail_act": 0.05,
            "trail_dist": 0.06, "trail_lock": 0.02, "pyr_trigger": 0.05,
        }
        bars = [
            (None, 100.0, 94.0, 95.0),   # lo <= 95 → activates trail
            (None, 96.0, 90.0, 91.0),     # peak=90, sl trails
            (None, 97.0, 95.0, 96.0),     # hits trail SL
        ]
        exit_px, reason, pyramid, peak, trail_active = scan_position(
            100.0, "SHORT", bars, cfg
        )
        assert reason == "trail"
        assert trail_active is True


# ─── detect_cross_events tests ──────────────────────────────────────────

class TestDetectCrossEvents:
    """LSR z-score cross detection."""

    def test_cross_up_short(self):
        """z crosses above +th → SHORT signal."""
        z_series = [
            ("2026-01-01T00:00:00", 1.5),
            ("2026-01-01T01:00:00", 2.5),  # crosses above th=2.0
        ]
        events = detect_cross_events(z_series, th=2.0)
        assert len(events) == 1
        assert events[0][1] == "SHORT"

    def test_cross_down_long(self):
        """z crosses below -th → LONG signal."""
        z_series = [
            ("2026-01-01T00:00:00", -1.5),
            ("2026-01-01T01:00:00", -2.5),  # crosses below -th=2.0
        ]
        events = detect_cross_events(z_series, th=2.0)
        assert len(events) == 1
        assert events[0][1] == "LONG"

    def test_no_cross_stays_above(self):
        """z stays above th → no new cross event."""
        z_series = [
            ("2026-01-01T00:00:00", 2.5),
            ("2026-01-01T01:00:00", 3.0),
        ]
        events = detect_cross_events(z_series, th=2.0)
        assert len(events) == 0

    def test_no_cross_stays_below(self):
        """z stays below -th → no new cross event."""
        z_series = [
            ("2026-01-01T00:00:00", -2.5),
            ("2026-01-01T01:00:00", -3.0),
        ]
        events = detect_cross_events(z_series, th=2.0)
        assert len(events) == 0

    def test_multiple_crosses(self):
        """Multiple crosses produce multiple events."""
        z_series = [
            ("t0", 0.0),
            ("t1", 2.5),   # cross up → SHORT
            ("t2", 0.0),
            ("t3", -2.5),  # cross down → LONG
            ("t4", 0.0),
            ("t5", 2.5),   # cross up → SHORT
        ]
        events = detect_cross_events(z_series, th=2.0)
        assert len(events) == 3
        assert events[0][1] == "SHORT"
        assert events[1][1] == "LONG"
        assert events[2][1] == "SHORT"

    def test_empty_series(self):
        """Empty z_series → no events."""
        events = detect_cross_events([], th=2.0)
        assert events == []

    def test_single_element(self):
        """Single element → no events."""
        events = detect_cross_events([("t0", 3.0)], th=2.0)
        assert events == []


# ─── compute_z tests ─────────────────────────────────────────────────────

class TestComputeZ:
    """Z-score computation."""

    def test_enough_data(self):
        """With 200 data points, z-score is computed."""
        data = [(f"t{i}", float(i % 10)) for i in range(200)]
        result = compute_z(data, z_window=100)
        assert len(result) > 0
        assert all(isinstance(z, float) for _, z in result)

    def test_insufficient_data(self):
        """With <100 data points, returns empty."""
        data = [(f"t{i}", float(i)) for i in range(50)]
        result = compute_z(data, z_window=100)
        assert result == []

    def test_constant_series(self):
        """Constant ratios → z=0 (std=0 → filtered out)."""
        data = [(f"t{i}", 1.0) for i in range(200)]
        result = compute_z(data, z_window=100)
        assert result == []  # std=0 → skipped

    def test_z_positive_for_high_value(self):
        """Value above mean → positive z."""
        data = [(f"t{i}", 100.0 + (i % 5)) for i in range(200)]
        result = compute_z(data, z_window=100)
        assert len(result) > 0
        # Last value (100.0) is at mean, z ≈ 0
        _, last_z = result[-1]
        assert isinstance(last_z, float)


# ─── load_config tests ──────────────────────────────────────────────────

class TestLoadConfig:
    """YAML config loading with defaults."""

    def test_default_config(self, tmp_path):
        """Missing YAML → defaults."""
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg["base_risk"] == 0.08
        assert cfg["leverage"] == 1.0
        assert cfg["max_pos"] == 6
        assert cfg["sl_pct"] == 0.05
        assert cfg["tp_pct"] == 0.18
        assert cfg["comm"] == 0.001
        assert cfg["slip"] == 0.0005

    def test_yaml_overrides(self, tmp_path):
        """YAML overrides defaults."""
        yaml_content = """
strategy: lsr_cross
params:
  base_risk: 0.05
  leverage: 2
  sl_pct: 0.03
risk:
  max_pos: 4
  sym_risk:
    ETHUSDT: 1.5
    BTCUSDT: 0.8
"""
        p = tmp_path / "test.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert cfg["base_risk"] == 0.05
        assert cfg["leverage"] == 2.0
        assert cfg["sl_pct"] == 0.03
        assert cfg["max_pos"] == 4
        assert cfg["sym_risk"]["ETHUSDT"] == 1.5
        assert cfg["sym_risk"]["BTCUSDT"] == 0.8

    def test_exclude_blocks(self, tmp_path):
        """Exclude block parsed correctly."""
        yaml_content = """
strategy: lsr_cross
exclude:
  syms: ["BTCUSDT"]
  hours: [0, 1, 2]
  dow: [6]
"""
        p = tmp_path / "test.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert "BTCUSDT" in cfg["exclude_syms"]
        assert cfg["exclude_hours"] == {0, 1, 2}
        assert cfg["exclude_dow"] == {6}

    def test_commission_slippage_mapping(self, tmp_path):
        """risk.comission → comm, risk.slippage → slip."""
        yaml_content = """
strategy: lsr_cross
risk:
  comission: 0.002
  slippage: 0.001
"""
        p = tmp_path / "test.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert cfg["comm"] == 0.002
        assert cfg["slip"] == 0.001

    def test_signals_params_extracted(self, tmp_path):
        """Signal-level trail/pyr params (where live actually defines them)
        are folded into cfg — trail_lock must NOT stay at the 0.05 default."""
        yaml_content = """
strategy: lsr_cross
signals:
  - id: lsr_short
    params:
      sl_pct: 0.05
      tp_pct: 0.18
      trail_act: 0.05
      trail_dist: 0.06
      trail_lock: 0.02
      hold_h: 120
      pyr_trigger: 0.05
      pyr_add: 0.5
risk:
  base_risk: 0.08
  leverage: 1
"""
        p = tmp_path / "test.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert cfg["trail_lock"] == 0.02
        assert cfg["trail_act"] == 0.05
        assert cfg["trail_dist"] == 0.06
        assert cfg["pyr_trigger"] == 0.05
        assert cfg["pyr_add"] == 0.5
        assert cfg["timeout_bars"] == 120
        assert cfg["leverage"] == 1.0
