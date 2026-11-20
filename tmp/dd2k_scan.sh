#!/bin/bash
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1 tag $2 dd_stop $3 dd_cool
  $PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 2000 --eq-cap 2000 \
    ${2:+--dd-stop $2} ${3:+--dd-cool $3} \
    --out "results/dd2k_$1.json" > "results/dd2k_$1.log" 2>&1
  echo "DONE $1"
}
run off   ""   ""
run s10c60 0.10 60
run s15c60 0.15 60
echo ALL_DONE
