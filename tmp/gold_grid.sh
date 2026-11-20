#!/bin/bash
# Сетка gold по всей истории 2019-01-01..2026-11-17 — для walk-forward по годам.
# ВАЖНО: self_tester грузит --days календарных дней до --end, поэтому глубина задаётся --days.
cd /home/user/projects/tqa-f || exit 1
export PYTHONPATH=. PYTHONUNBUFFERED=1
unset FOREX_BARS_TABLE TREND_DEBUG http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
PY=/home/user/venvs/tqa/main/bin/python
mkdir -p results/gg
run_one() {
  f=$1; s=$2; m=$3
  out=results/gg/${f}_${s}_${m}.json
  [ -s "$out" ] && return 0
  $PY strategies/trend_sma/self_tester.py --tickers xauusd \
    --sma-by-ticker "xauusd=${f}/${s}/${m}" --start 2019-01-01 --end 2026-11-17 \
    --days 3200 --fixed-lots 1 --sl-pct 1.5 --arm-pct 2.0 --trail-pct 1.5 \
    --out "$out" > "results/gg/${f}_${s}_${m}.log" 2>&1
}
export -f run_one
export PY
for f in 10 20 35 50; do
  for s in 60 100 150 200; do
    for m in sma ema; do
      echo "$f $s $m"
    done
  done
done | xargs -n 3 -P 10 bash -c 'run_one "$@"' _
echo "GRID DONE $(ls results/gg/*.json 2>/dev/null | wc -l) из 32"
