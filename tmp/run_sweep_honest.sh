#!/bin/bash
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
PY=/home/user/venvs/tqa/main/bin/python
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PAIRS="audjpy audusd euraud eurgbp eurjpy eurusd gbpjpy gbpusd nzdusd usdcad usdchf usdjpy xauusd"
PAR=5
run=0
for p in $PAIRS; do
  $PY scripts/sma_sweep.py --tickers "$p" \
      --fast 10,20,35,50 --slow 60,100,150,200 --ma sma,ema \
      --is-start 2020-01-01 --is-end 2024-12-31 --year 2020-2024 \
      --min-n 20 --top 3 \
      --out "results/is2020_24_$p.json" > "results/is2020_24_$p.log" 2>&1 < /dev/null &
  run=$((run+1))
  if [ "$run" -ge "$PAR" ]; then wait -n; run=$((run-1)); fi
done
wait
echo DONE
