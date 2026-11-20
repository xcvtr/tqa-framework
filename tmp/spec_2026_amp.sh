#!/bin/bash
# 2026, старт 2000: усилители — пирамидинг и компаунд (трейлинг уже включён по умолчанию).
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2026-01-01 --end 2026-11-17 --days 420 --fixed-lots 0.01 --pyramid-max 3 \
  --eq0 2000 --eq-cap 2000 \
  --out results/spec_2026_2000_pyr.json > results/spec_2026_2000_pyr.log 2>&1
echo DONE pyr
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2026-01-01 --end 2026-11-17 --days 420 --fixed-lots 0.0 --lot-step 0.01 --eq-cap 0 \
  --eq0 2000 \
  --out results/spec_2026_2000_comp.json > results/spec_2026_2000_comp.log 2>&1
echo DONE comp
$PY -u strategies/trend_sma/self_tester.py --tickers xauusd --sma 35/60 --ma-type sma \
  --start 2026-01-01 --end 2026-11-17 --days 420 --fixed-lots 0.0 --lot-step 0.01 --eq-cap 0 \
  --pyramid-max 3 --eq0 2000 \
  --out results/spec_2026_2000_both.json > results/spec_2026_2000_both.log 2>&1
echo DONE both
echo ALL_DONE
