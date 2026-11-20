#!/bin/bash
# xauusd 50/60sma, OOS 2026-01-01..2026-11-17, pyramiding 3 ступени + trailing 2.0/1.5 + SL 1.5
# C1 компаунд (дробный лот 0.01, сайзинг от equity) + пирамидинг, 475
# C2 то же без пирамидинга
# C3 фикс 0.01 (без компаунда) + пирамидинг
# C4 компаунд + пирамидинг, старт 2000
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
base="--tickers xauusd --sma-by-ticker xauusd=50/60/sma --start 2026-01-01 --end 2026-11-17 \
 --risk-pct 0.025 --sl-pct 1.5 --trail-pct 1.5 --arm-pct 2.0"
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 0 --eq0 475 --fixed-lots 0 \
  --lot-step 0.01 --pyramid-max 3 --out results/cmp_C1.json > results/cmp_C1.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 0 --eq0 475 --fixed-lots 0 \
  --lot-step 0.01 --no-pyramid --out results/cmp_C2.json > results/cmp_C2.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 200000 --eq0 475 --fixed-lots 0.01 \
  --pyramid-max 3 --out results/cmp_C3.json > results/cmp_C3.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 0 --eq0 2000 --fixed-lots 0 \
  --lot-step 0.01 --pyramid-max 3 --out results/cmp_C4.json > results/cmp_C4.log 2>&1 < /dev/null &
sleep 2
echo "LAUNCHED $(pgrep -fc self_tester)"
