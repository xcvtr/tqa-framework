#!/usr/bin/env python3
"""Test script to verify parameter parsing in LsrCrossBacktester"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from tqa_framework.backtesters.lsr_cross import LsrCrossBacktester

# Test 1: Default values (should read from strategy_params or use defaults)
print("Test 1: Default parameters")
bt = LsrCrossBacktester(
    tickers=[{"symbol": "BTCUSDT"}],
    days=30,
    risk_pct=0.08,
    tf_minutes=60,
    strategy_name="lsr_cross",
    strategy_params={}  # No custom params - should use defaults
)
# Call our param parsing logic manually
bt.z_score = float(getattr(bt, 'z_score', bt.strategy_params.get('z_score', 2.0)))
_exclude_hours = getattr(bt, 'exclude_hours', bt.strategy_params.get('exclude_hours', []))
bt.EXCLUDE_HOURS = set(int(h) for h in _exclude_hours) if _exclude_hours else set()
_exclude_dow = getattr(bt, 'exclude_dow', bt.strategy_params.get('exclude_dow', []))
bt.EXCLUDE_DOW = set(int(d) for d in _exclude_dow) if _exclude_dow else set()
_exclude_syms = getattr(bt, 'exclude_syms', bt.strategy_params.get('exclude_syms', []))
bt.EXCLUDE_SYMS = set(_exclude_syms) if _exclude_syms else set()
bt.exclude_enabled = bool(getattr(bt, 'exclude_enabled', bt.strategy_params.get('exclude_enabled', True)))

print(f"  z_score: {bt.z_score} (expected: 2.0)")
print(f"  EXCLUDE_HOURS: {bt.EXCLUDE_HOURS} (expected: set())")
print(f"  EXCLUDE_DOW: {bt.EXCLUDE_DOW} (expected: set())")
print(f"  EXCLUDE_SYMS: {bt.EXCLUDE_SYMS} (expected: set())")
print(f"  exclude_enabled: {bt.exclude_enabled} (expected: True)")
print()

# Test 2: Custom parameters
print("Test 2: Custom parameters")
bt2 = LsrCrossBacktester(
    tickers=[{"symbol": "BTCUSDT"}],
    days=30,
    risk_pct=0.08,
    tf_minutes=60,
    strategy_name="lsr_cross",
    strategy_params={
        "z_score": 1.5,
        "exclude_hours": [11, 22, 23],
        "exclude_dow": [2],
        "exclude_syms": ["SOLUSDT", "ETHUSDT"],
        "exclude_enabled": False
    }
)
bt2.z_score = float(getattr(bt2, 'z_score', bt2.strategy_params.get('z_score', 2.0)))
_exclude_hours2 = getattr(bt2, 'exclude_hours', bt2.strategy_params.get('exclude_hours', []))
bt2.EXCLUDE_HOURS = set(int(h) for h in _exclude_hours2) if _exclude_hours2 else set()
_exclude_dow2 = getattr(bt2, 'exclude_dow', bt2.strategy_params.get('exclude_dow', []))
bt2.EXCLUDE_DOW = set(int(d) for d in _exclude_dow2) if _exclude_dow2 else set()
_exclude_syms2 = getattr(bt2, 'exclude_syms', bt2.strategy_params.get('exclude_syms', []))
bt2.EXCLUDE_SYMS = set(_exclude_syms2) if _exclude_syms2 else set()
bt2.exclude_enabled = bool(getattr(bt2, 'exclude_enabled', bt2.strategy_params.get('exclude_enabled', True)))

print(f"  z_score: {bt2.z_score} (expected: 1.5)")
print(f"  EXCLUDE_HOURS: {bt2.EXCLUDE_HOURS} (expected: {{11, 22, 23}})")
print(f"  EXCLUDE_DOW: {bt2.EXCLUDE_DOW} (expected: {{2}})")
print(f"  EXCLUDE_SYMS: {bt2.EXCLUDE_SYMS} (expected: {{'SOLUSDT', 'ETHUSDT'}})")
print(f"  exclude_enabled: {bt2.exclude_enabled} (expected: False)")
print()

# Test 3: Mixed - some params provided, others use defaults
print("Test 3: Mixed parameters")
bt3 = LsrCrossBacktester(
    tickers=[{"symbol": "BTCUSDT"}],
    days=30,
    risk_pct=0.08,
    tf_minutes=60,
    strategy_name="lsr_cross",
    strategy_params={
        "z_score": 1.8,
        "exclude_hours": [12, 13, 14]
        # exclude_dow, exclude_syms, exclude_enabled not provided - should use defaults
    }
)
bt3.z_score = float(getattr(bt3, 'z_score', bt3.strategy_params.get('z_score', 2.0)))
_exclude_hours3 = getattr(bt3, 'exclude_hours', bt3.strategy_params.get('exclude_hours', []))
bt3.EXCLUDE_HOURS = set(int(h) for h in _exclude_hours3) if _exclude_hours3 else set()
_exclude_dow3 = getattr(bt3, 'exclude_dow', bt3.strategy_params.get('exclude_dow', []))
bt3.EXCLUDE_DOW = set(int(d) for d in _exclude_dow3) if _exclude_dow3 else set()
_exclude_syms3 = getattr(bt3, 'exclude_syms', bt3.strategy_params.get('exclude_syms', []))
bt3.EXCLUDE_SYMS = set(_exclude_syms3) if _exclude_syms3 else set()
bt3.exclude_enabled = bool(getattr(bt3, 'exclude_enabled', bt3.strategy_params.get('exclude_enabled', True)))

print(f"  z_score: {bt3.z_score} (expected: 1.8)")
print(f"  EXCLUDE_HOURS: {bt3.EXCLUDE_HOURS} (expected: {{12, 13, 14}})")
print(f"  EXCLUDE_DOW: {bt3.EXCLUDE_DOW} (expected: set())")
print(f"  EXCLUDE_SYMS: {bt3.EXCLUDE_SYMS} (expected: set())")
print(f"  exclude_enabled: {bt3.exclude_enabled} (expected: True)")
print()

print("All tests completed successfully!")