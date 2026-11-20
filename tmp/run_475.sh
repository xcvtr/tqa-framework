#!/bin/bash
cd /home/user/projects/tqa-f || exit 1
PY=/home/user/venvs/tqa/main/bin/python
export PYTHONPATH=.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
LOG=results/port_475.log
: > "$LOG"
echo "=== 475 USD, 0.01 лота, пул по 2020-2024 ===" >> "$LOG"
$PY strategies/trend_sma/self_tester.py --tickers audjpy,audusd,eurusd,gbpjpy,gbpusd \
  --sma-by-ticker "audjpy=20/100/ema,audusd=10/60/ema,eurusd=10/60/sma,gbpjpy=35/60/sma,gbpusd=50/60/sma" \
  --start 2026-01-01 --end 2026-11-17 --fixed-lots 0.01 --eq-cap 475 \
  --out results/pool475_long.json >> "$LOG" 2>&1
echo "=== 475 USD, 0.01 лота, пул по 2026 ===" >> "$LOG"
$PY strategies/trend_sma/self_tester.py --tickers audusd,euraud,eurgbp,xauusd \
  --sma-by-ticker "audusd=10/60/ema,euraud=10/100/ema,eurgbp=20/100/ema,xauusd=50/100/sma" \
  --start 2026-01-01 --end 2026-11-17 --fixed-lots 0.01 --eq-cap 475 \
  --out results/pool475_oos.json >> "$LOG" 2>&1
echo DONE >> "$LOG"
