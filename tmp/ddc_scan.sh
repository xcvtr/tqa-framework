#!/bin/bash
# Стоп по просадке с принудительным возвратом по времени, капитал 475.
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1 tag $2 dd_stop $3 dd_cool
  $PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 475 --eq-cap 475 \
    --dd-stop "$2" --dd-cool "$3" \
    --out "results/ddc_$1.json" > "results/ddc_$1.log" 2>&1
  echo "DONE $1"
}
run s10c60  0.10 60
run s15c60  0.15 60
run s15c120 0.15 120
run s25c120 0.25 120
echo ALL_DONE
