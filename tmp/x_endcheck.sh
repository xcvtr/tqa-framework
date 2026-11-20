#!/bin/bash
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
/home/user/venvs/tqa/main/bin/python strategies/trend_sma/self_tester.py --tickers xauusd \
  --sma-by-ticker "xauusd=50/60/sma" --start 2026-01-01 --end 2026-11-30 --fixed-lots 1 \
  --out results/verify_xauusd_end1130.json > results/verify_xauusd_end1130.log 2>&1
echo DONE >> results/verify_xauusd_end1130.log
