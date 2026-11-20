#!/bin/bash
# Старт 2000 USD, фикс-лот 0.01, спека AlfaForex. 2026 отдельно + вся история.
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2026-01-01 --end 2026-11-17 --days 420 --fixed-lots 0.01 --eq0 2000 --eq-cap 2000 \
  --out results/spec_2026_2000.json > results/spec_2026_2000.log 2>&1
echo DONE 2026
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2019-01-01 --end 2026-11-17 --days 3200 --fixed-lots 0.01 --eq0 2000 --eq-cap 2000 \
  --out results/spec_2000_full.json > results/spec_2000_full.log 2>&1
echo DONE full
echo ALL_DONE
