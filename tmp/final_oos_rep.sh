#!/bin/bash
# Финальный замер OOS на ЗАКРЫТОМ дне (2026-11-17), все 12 отобранных на IS 2020-2024 конфигов.
# Отбор не пересматривается: конфиг берётся из results/is2020_24_<pair>.json (best[0]).
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1=pair $2=fast/slow/ma
  setsid nohup $PY strategies/trend_sma/self_tester.py --tickers "$1" \
    --sma-by-ticker "$1=$2" --start 2026-01-01 --end 2026-11-17 --fixed-lots 1 \
    --out "results/oos_rep_$1.json" > "results/oos_rep_$1.log" 2>&1 < /dev/null &
}
run audjpy  10/100/sma
run audusd  10/60/ema
run euraud  35/150/sma
run eurgbp  10/60/sma
run eurjpy  20/200/sma
run eurusd  10/200/ema
run gbpjpy  20/200/ema
run gbpusd  10/100/ema
run usdcad  20/60/sma
run usdchf  10/100/sma
run usdjpy  10/100/sma
run xauusd  50/60/sma
sleep 2
echo "LAUNCHED $(pgrep -fc self_tester)"
