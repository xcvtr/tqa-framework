#!/bin/bash
cd /home/user/projects/tqa-f || exit 1
PY=/home/user/venvs/tqa/main/bin/python
export PYTHONPATH=.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
LOG=results/port_pairs.log
: > "$LOG"

echo "=== POOL_LONG (отбор по 2020-2024) ===" >> "$LOG"
$PY strategies/trend_sma/self_tester.py \
  --tickers audjpy,audusd,eurusd,gbpjpy,gbpusd \
  --sma-by-ticker "audjpy=20/100/ema,audusd=10/60/ema,eurusd=10/60/sma,gbpjpy=35/60/sma,gbpusd=50/60/sma" \
  --start 2026-01-01 --end 2026-11-17 --fixed-lots 1 \
  --out results/pool_long_trades.json >> "$LOG" 2>&1

echo "=== POOL_OOS (отбор по 2026) ===" >> "$LOG"
$PY strategies/trend_sma/self_tester.py \
  --tickers audusd,euraud,eurgbp,xauusd \
  --sma-by-ticker "audusd=10/60/ema,euraud=10/100/ema,eurgbp=20/100/ema,xauusd=50/100/sma" \
  --start 2026-01-01 --end 2026-11-17 --fixed-lots 1 \
  --out results/pool_oos_trades.json >> "$LOG" 2>&1

echo DONE >> "$LOG"
