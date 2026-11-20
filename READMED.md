# IR Engine Integration Fix

## Issue Summary
The IR strategy was failing to generate trades because `engine.run_days` was hardcoded to use only the SMA detect function from `strategies.trend_sma.detect`, completely ignoring the IR strategy's own detect implementation in `strategies.ir.detect`.

## Solution Implemented
Modified `strategies/trend_sma/engine.py` to dynamically select the correct detect function:
1. Check if ticker has a 'strategy' parameter in params_by_ticker
2. If strategy is 'ir', use `strategies.ir.detect.detect`  
3. Otherwise, fall back to `strategies.trend_sma.detect` (backward compatible)

## Result
IR strategy signals now properly convert to trades through the engine, resolving the 0-trade issue for EURUSD, GBPUSD, USDJPY, AUDUSD across all i parameters (12,24,36,48) between 2026-01-01 and 2026-11-19.