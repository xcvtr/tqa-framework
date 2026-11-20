#!/bin/bash
# База после обновления спеки AlfaForex (спред 4 пп, комиссия 0, своп, маржа 6%).
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
for cap in 475 4000; do
  $PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 "$cap" --eq-cap "$cap" \
    --out "results/spec_$cap.json" > "results/spec_$cap.log" 2>&1
  echo "DONE $cap"
done
echo ALL_DONE
