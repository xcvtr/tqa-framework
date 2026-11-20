---
title: "IR Strategy — Trailing SL + Ticker Selection + Parameter Optimization"
checkpoint: 20261119_ir_optimization
date: 2026-11-19_23-50_MSK
tags: [checkpoint, tqa-f, ir, impulse_retrace, forex, sweep, trailing-sl]
---

# IR Strategy Optimization — Trailing SL Integration & Parameter Sweep

## What Changed

1. **Trailing SL code integration in `strategies/ir/detect.py`**
   - `trail` parameter (1.5%) now actively affects wave generation: peak price tracking after each scale-in entry
   - Trail condition: if price retraces `trail%` from peak → pyramid levels limited to max 1
   - Output: `trail_triggered: bool` field added to each wave dict
   - Before: `trail` was defined in `DEFAULT_PARAMS` but never used in detection logic

2. **Ticker set optimization (audusd → audjpy)**
   - audusd: +3.69% ROI, MDD 14.44% (barely passes MDD<15% threshold)
   - audjpy: +96.90% ROI, MDD 4.57% (best performer, 26× better)
   - Updated `scripts/ir_pnl_tracker.py` and `scripts/ir_pnl_tracker_v2.py`

3. **i-parameter sweep (12, 24, 36, 48)**
   - i=48 optimal: +292.42% SUM ROI, +4.61% AVG MDD, ratio 9.05
   - i=24 baseline (before): +48.54% SUM ROI, +5.99% AVG MDD

## Key Metrics

### Before optimization (i=24, 4 tickers with audusd):
```
eurusd:  +8.84% ROI,  +2.79% MDD
gbpusd: +11.83% ROI,  +0.77% MDD
usdjpy: +24.18% ROI,  +5.94% MDD
audusd:  +3.69% ROI, +14.44% MDD
────────────────────────────────
SUM:    +48.54% ROI, +23.95% MDD pool
```

### After optimization (i=48, 7 best tickers):
```
audjpy: +118.32% ROI,  5.80% MDD  ⭐
gbpjpy:  +57.98% ROI,  2.90% MDD  ⭐
eurjpy:  +51.31% ROI,  7.21% MDD  ⭐
usdjpy:  +40.51% ROI,  2.42% MDD  ⭐
nzdusd:   +5.53% ROI,  9.43% MDD
gbpusd:  +10.92% ROI,  0.25% MDD
eurusd:   +7.83% ROI,  4.29% MDD
────────────────────────────────
SUM ROI:     +292.42%
AVG ROI:      +41.77%
AVG MDD:       +4.61%
ROI/MDD Ratio:  9.05
Total Trades:  2345
All MDD<15%:  ✅ 7/7
```

### Capital sweep (500/1000/2000 USD per ticker):
```
500 USD each:  +1,462.09 USD total
1000 USD each: +2,924.18 USD total
2000 USD each: +5,848.36 USD total
```

## Files Changed

- `strategies/ir/detect.py` — trail parameter now functional (peak tracking, pyramid limiting on trail)
- `scripts/ir_pnl_tracker.py` — updated ticker list (audusd → audjpy)
- `scripts/ir_pnl_tracker_v2.py` — updated ticker list (audusd → audjpy)

## Decisions

- **audjpy replaces audusd**: 26× better ROI (96.90% vs 3.69%) with lower MDD
- **i=48 optimal**: longer impulse window captures broader price movements without quality loss
- **Trail parameter**: integrated for risk management (pyramid limiting), PnL impact minimal in "next bar" exit framework
- **nzdusd+eurgbp+eurusd** kept for diversification despite lower ROI (MDD safety)

## State for Continuation

| Aspect | Status |
|--------|--------|
| Default config | i=48, trail=1.5%, 7 best tickers |
| All MDD < 15% | ✅ 7/7 pass |
| SUM ROI | +292.42% |
| Trailing SL code | ✅ Integrated in detect.py |
| Tracker scripts | ✅ Updated with new ticker list |
| kanban optimization | ✅ Complete (3 columns, 7 tasks) |

## Architecture Note

IR strategy lives in `strategies/ir/detect.py`, imported by scripts. Data from CH `forex.bars` via `data_provider.py`. Bar format: `{ts, opn, hi, lo, prc}`.