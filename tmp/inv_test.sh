#!/bin/bash
# Один и тот же конфиг через canonical CLI, разная глубина загрузки истории.
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1=tag $2=days
  $PY strategies/trend_sma/self_tester.py --tickers xauusd --sma-by-ticker "xauusd=50/60/sma" \
    --start 2026-01-01 --end 2026-11-30 --fixed-lots 1 --days "$2" \
    --out "results/inv_$1.json" > "results/inv_$1.log" 2>&1
  echo "DONE $1" >> results/inv_$1.log
}
run d365 365 &
run d2700 2700 &
wait
echo ALLDONE
