#!/bin/bash
# Сканы стартового капитала: где DD укладывается в 20% и с какого equity включается компаунд.
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
base="--tickers xauusd --sma-by-ticker xauusd=50/60/sma --start 2026-01-01 --end 2026-11-17 \
 --risk-pct 0.025 --sl-pct 1.5 --trail-pct 1.5 --arm-pct 2.0 --eq-cap 0 --fixed-lots 0 \
 --lot-step 0.01 --pyramid-max 3"
for e in 1250 4000 10000 20000; do
  setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq0 $e \
    --out results/cmp_E$e.json > results/cmp_E$e.log 2>&1 < /dev/null &
done
sleep 2
echo "LAUNCHED $(pgrep -fc self_tester)"
