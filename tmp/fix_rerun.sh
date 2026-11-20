#!/bin/bash
# Перегон xauusd на ЧИСТЫХ данных (forex.bars_fix: март–август 2026 из bars_partial).
# D1 трейлинг, лот 0.01, 475        — база
# D2 трейлинг+пирамидинг+компаунд, 475
# D3 то же, 4000                     — под лимит DD 20%
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1 FOREX_BARS_TABLE=forex.bars_fix
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
base="--tickers xauusd --sma-by-ticker xauusd=50/60/sma --start 2026-01-01 --end 2026-11-17 \
 --risk-pct 0.025 --sl-pct 1.5 --trail-pct 1.5 --arm-pct 2.0"
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 200000 --eq0 475 --fixed-lots 0.01 \
  --out results/fix_D1.json > results/fix_D1.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 0 --eq0 475 --fixed-lots 0 \
  --lot-step 0.01 --pyramid-max 3 --out results/fix_D2.json > results/fix_D2.log 2>&1 < /dev/null &
setsid nohup $PY strategies/trend_sma/self_tester.py $base --eq-cap 0 --eq0 4000 --fixed-lots 0 \
  --lot-step 0.01 --pyramid-max 3 --out results/fix_D3.json > results/fix_D3.log 2>&1 < /dev/null &
sleep 2
echo "LAUNCHED $(pgrep -fc self_tester)"
