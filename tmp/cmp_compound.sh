#!/bin/bash
# xauusd 50/60sma, 475 USD, OOS до закрытого дня 2026-11-17.
# A = компаунд (сайзинг от equity, eq-cap 0) + пирамидинг + trailing
# B = то же без пирамидинга (чтобы видеть вклад)
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
common="--tickers xauusd --sma-by-ticker xauusd=50/60/sma --start 2026-01-01 --end 2026-11-17 \
  --eq-cap 0 --fixed-lots 0 --risk-pct 0.025 --sl-pct 1.5 --trail-pct 1.5 --arm-pct 2.0"
setsid nohup $PY strategies/trend_sma/self_tester.py $common \
  --out results/oos_cmp_A.json > results/oos_cmp_A.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $common --no-pyramid \
  --out results/oos_cmp_B.json > results/oos_cmp_B.log 2>&1 < /dev/null &
sleep 2
echo "LAUNCHED $(pgrep -fc self_tester)"
