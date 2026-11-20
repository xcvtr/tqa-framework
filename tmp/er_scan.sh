#!/bin/bash
# Скан порога защиты от боковика (ER 50 дней) на золоте, вся история.
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
for er in 0 0.15 0.2 0.25 0.3 0.35; do
  ( $PY -u strategies/trend_sma/self_tester.py --tickers xauusd \
      --sma 35/60 --ma-type sma \
      --start 2019-01-01 --end 2026-11-17 --days 3200 \
      --fixed-lots 0.01 --eq0 475 --eq-cap 475 \
      --regime-er $er \
      --out results/er_${er}.json > results/er_${er}.log 2>&1
    echo "DONE er=$er" ) &
done
wait
echo ALL_DONE
