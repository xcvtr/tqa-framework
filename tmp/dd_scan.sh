#!/bin/bash
# Стоп по просадке счёта: сетка порогов на золоте, два капитала.
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1 tag $2 eq0 $3 dd_stop
  $PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 "$2" --eq-cap "$2" \
    ${3:+--dd-stop $3} \
    --out "results/dd_$1.json" > "results/dd_$1.log" 2>&1
  echo "DONE $1"
}
for cap in 475 4000; do
  run "${cap}_off" "$cap" ""
  for s in 0.10 0.15 0.20; do
    run "${cap}_s$s" "$cap" "$s"
  done
done
echo ALL_DONE
