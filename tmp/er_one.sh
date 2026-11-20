#!/bin/bash
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2019-01-01 --end 2026-11-17 --days 3200 \
  --fixed-lots 0.01 --eq0 475 --eq-cap 475 --regime-er 0 \
  --out results/er_0.json > results/er_0.log 2>&1
echo "DONE er=0"
