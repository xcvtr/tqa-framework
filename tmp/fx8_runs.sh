#!/bin/bash
# 12 пар, 8 лет, фикс-лот 0.01: база + два гейта (r2, vr5). Один прогон на вариант.
cd /home/user/projects/tqa-f
export PYTHONPATH=.
PY=/home/user/venvs/tqa/main/bin/python
SMA="audjpy=10/100,audusd=10/60,euraud=35/150,eurgbp=10/60,eurjpy=20/200,eurusd=10/200,gbpjpy=20/200,gbpusd=10/100,usdcad=20/60,usdchf=10/100,usdjpy=10/100,xauusd=50/60"
TK="audjpy,audusd,euraud,eurgbp,eurjpy,eurusd,gbpjpy,gbpusd,usdcad,usdchf,usdjpy,xauusd"
run() {
  $PY -u strategies/trend_sma/self_tester.py --tickers "$TK" --sma-by-ticker "$SMA" --ma-type sma \
    --start 2019-01-01 --end 2026-11-17 --days 3200 \
    --fixed-lots 0.01 --eq0 20000 --eq-cap 20000 \
    $2 \
    --out results/fx8_$1.json > results/fx8_$1.log 2>&1
  echo "DONE $1"
}
run base ""
run r2 "--regime-metric r2 --regime-op <= --regime-thr 0.40"
run vr5 "--regime-metric vr5 --regime-op >= --regime-thr 1.05"
echo ALL_DONE
