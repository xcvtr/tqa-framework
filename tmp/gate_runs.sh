#!/bin/bash
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
run() {  # $1 = tag, $2 = metric, $3 = op, $4 = thr
  $PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 475 --eq-cap 475 \
    --regime-metric "$2" --regime-op "$3" --regime-thr "$4" \
    --out results/g_$1.json > results/g_$1.log 2>&1
  echo "DONE $1"
}
run sig_le  sig '<=' 0.81
run r2_le   r2  '<=' 0.40
run vr5_ge  vr5 '>=' 1.05
echo ALL_DONE
