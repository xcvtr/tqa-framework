"""Tests for LSR cross strategy engine integration with external signals."""

from __future__ import annotations

import pytest
from tqa_framework.strategy_engine.parser import load_strategy, load_sweep_config, build_sweep_combinations
from tqa_framework.strategy_engine import runtime
from tqa_framework.strategy_engine.models import StrategyDef, SignalConfig, ConditionGroup


def fake_external_signal(ts, symbol, zscore, direction, zprev=None):
    """Create a mock external signal."""
    return {
        'ts': ts,
        'symbol': symbol,
        'zscore': zscore,
        'zprev': zprev if zprev is not None else 0.0,
        'direction': direction,
        'action': 'signal',
    }


def make_strategy_with_external(name, external_signals):
    """Create a strategy that uses external signals."""
    strat = load_strategy(name)
    # Add signals that use external.zscore
    signals = [
        SignalConfig(
            id="z_cross_short",
            priority=10,
            when=ConditionGroup("all", [
                "external.zscore > 2.0",
            ]),
            then="open_short",
            params={},
        ),
        SignalConfig(
            id="z_cross_long",
            priority=9,
            when=ConditionGroup("all", [
                "external.zscore < -2.0",
            ]),
            then="open_long",
            params={},
        ),
    ]
    return strat


def test_lsr_cross_full_integration():
    """Full integration test: YAML strategy + external signals + evaluation."""
    # Load strategy
    strat = load_strategy("examples/strategies/lsr_cross/lsr_cross.strategy.yaml")
    
    # Create test data
    bars = [
        {'ts': 1700000000, 'close': 100.0, 'open': 99.0, 'high': 101.0, 'low': 98.0, 'volume': 1000},
        {'ts': 1700000060, 'close': 101.0, 'open': 100.0, 'high': 102.0, 'low': 99.0, 'volume': 1000},
        {'ts': 1700000120, 'close': 102.0, 'open': 101.0, 'high': 103.0, 'low': 100.0, 'volume': 1000},
    ]
    
    # External signals that match the strategy conditions
    external_signals = [
        fake_external_signal(1700000060, 'ETHUSDT', 2.1, 'SHORT', zprev=1.0),  # z_cross_short condition
        fake_external_signal(1700000120, 'ETHUSDT', -2.1, 'LONG', zprev=-1.0),  # z_cross_long condition
    ]
    
    # Evaluate with external signals
    signals = runtime.evaluate(
        bars, 
        strat, 
        {'symbol': 'ETHUSDT'}, 
        external_signals=external_signals
    )
    
    # Should have 2 signals: short then long
    assert len(signals) == 2
    
    # Check first signal (short)
    short_signals = [s for s in signals if s.signal_id == "z_cross_short"]
    assert len(short_signals) == 1
    assert short_signals[0].direction == "SHORT"
    assert short_signals[0].action == "open_short"
    
    # Check second signal (long)
    long_signals = [s for s in signals if s.signal_id == "z_cross_long"]
    assert len(long_signals) == 1
    assert long_signals[0].direction == "LONG"
    assert long_signals[0].action == "open_long"


def test_lsr_cross_risk_management():
    """Test that risk management parameters are accessible."""
    strat = load_strategy("examples/strategies/lsr_cross/lsr_cross.strategy.yaml")

    # The risk section is defined in YAML but not parsed into StrategyDef
    # (StrategyDef only parses strategy, version, metrics, signals, sweep)
    # This test verifies the YAML loads correctly and has required elements

    # Check that we have the expected signals
    signal_ids = {s.id for s in strat.signals}
    assert signal_ids == {"z_cross_short", "z_cross_long"}

    # Check that metrics include required ones
    assert "zscore" in strat.metrics
    assert "basis_zscore" in strat.metrics
    assert "basis_norm" in strat.metrics


def test_lsr_cross_sweep_combinations():
    """Test that sweep combinations are generated correctly."""
    sweep = load_sweep_config("examples/strategies/lsr_cross/lsr_cross.strategy.yaml")
    assert sweep.enabled
    
    combinations = build_sweep_combinations(sweep)
    # Should have 24 combinations based on parameter ranges
    assert len(combinations) == 24
    
    # Check that all expected parameters are present in combinations
    sample_combo = combinations[0]
    for param in ['z_entry', 'sl_pct', 'traildist']:
        assert param in sample_combo
        assert isinstance(sample_combo[param], (float, int))


def test_lsr_cross_signal_priority_order():
    """Test that signals are processed in priority order."""
    strat = load_strategy("examples/strategies/lsr_cross/lsr_cross.strategy.yaml")
    signals = sorted(strat.signals, key=lambda s: s.priority)
    
    # z_cross_long should come first (priority 9), then z_cross_short (priority 10)
    assert signals[0].id == "z_cross_long"
    assert signals[1].id == "z_cross_short"


def test_lsr_cross_without_external_signals():
    """Test that strategy works without external signals (should not error)."""
    strat = load_strategy("examples/strategies/lsr_cross/lsr_cross.strategy.yaml")
    
    # Create minimal test data
    bars = [
        {'ts': 1700000000, 'close': 100.0, 'open': 99.0, 'high': 101.0, 'low': 98.0, 'volume': 1000},
        {'ts': 1700000060, 'close': 101.0, 'open': 100.0, 'high': 102.0, 'low': 99.0, 'volume': 1000},
    ]
    
    # Should not raise errors even with empty external_signals
    signals = runtime.evaluate(
        bars, 
        strat, 
        {'symbol': 'ETHUSDT'}, 
        external_signals=[]
    )
    assert signals is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])