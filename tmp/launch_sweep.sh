#!/bin/bash
# Запуск свипа по парам отдельными сессиями (setsid), чтобы дети не умирали с группой.
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset TREND_DEBUG
PY=/home/user/venvs/tqa/main/bin/python
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PAIRS="${1:-audjpy audusd euraud eurgbp eurjpy eurusd gbpjpy gbpusd nzdusd usdcad usdchf usdjpy xauusd}"
for p in $PAIRS; do
  setsid nohup $PY scripts/sma_sweep.py --tickers "$p" \
      --fast 10,20,35,50 --slow 60,100,150,200 --ma sma,ema \
      --is-start 2020-01-01 --is-end 2024-12-31 --year 2020-2024 \
      --min-n 20 --top 3 \
      --out "results/is2020_24_$p.json" > "results/is2020_24_$p.log" 2>&1 < /dev/null &
done
sleep 2
echo "LAUNCHED $(pgrep -fc sma_sweep)"
