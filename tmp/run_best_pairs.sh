#!/bin/bash
cd /home/user/projects/tqa-f || exit 1
PY=/home/user/venvs/tqa/main/bin/python
export PYTHONPATH=.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
LOG=results/best_pairs.log; : > "$LOG"
run(){ echo "=== $1 ===" >> "$LOG"; shift; $PY strategies/trend_sma/self_tester.py "$@" \
  --start 2026-01-01 --end 2026-11-17 --fixed-lots 0.01 --eq-cap 475 >> "$LOG" 2>&1; }
run "TOP2 по 2020-2024: audjpy, gbpusd" --tickers audjpy,gbpusd --sma-by-ticker "audjpy=20/100/ema,gbpusd=50/60/sma"
run "TOP3 по 2020-2024: + audusd" --tickers audjpy,gbpusd,audusd --sma-by-ticker "audjpy=20/100/ema,gbpusd=50/60/sma,audusd=10/60/ema"
run "ТОЛЬКО xauusd (лучшая по 2026)" --tickers xauusd --sma-by-ticker "xauusd=50/100/sma"
echo DONE >> "$LOG"
