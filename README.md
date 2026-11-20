# IR Strategy Backtest Results

Comprehensive backtest of the IR (Impulse Retrace) strategy with the fixed _build_waves function.

## Parameters
- Tickers: EURUSD, GBPUSD, USDJPY, AUDUSD
- Date Range: 2026-01-01 to 2026-11-19
- Capital: $500
- No commissions or swaps
- i parameter swept: 12, 24, 36, 48

## Issue Fixed
The _build_waves function in strategies/ir/detect.py previously contained a bug where it checked `n < i+1` but incorrectly handled the edge case of insufficient bars.

## Key Changes
1. Added `if n < i + 1: return waves` check to prevent errors when insufficient data exists
2. Ensured data provider key format mapping (opn→open, prc→close) to work with the detect module
3. Implemented proper parameter sweep across all tickers and parameter values

## Results
Final results saved to `final_results.json` with structures for each ticker and i value containing:
- ROI (%)
- MDD ($)
- Total signals count
- ROI/MDD ratio

All metrics were processed using the fixed IR detection module with over 320K+ bars of data.