#!/usr/bin/env python3
"""Честная оценка фильтра режима на ОДНОМ конфиге (35/60/sma), без суммирования по сетке:
пп/сделку, t-статистика, число сделок по годам + распределение ER по годам."""
import json
import datetime
import statistics as st
from collections import defaultdict

from strategies.trend_sma.data_provider import ch_client

CFG = "35/60/sma"
c = ch_client()
rows = c.query(
    "SELECT toUnixTimestamp(toDate(time)) d, max(high) h, min(low) l, argMax(close, time) px "
    "FROM forex.bars WHERE symbol='xauusd' AND time>='2018-06-01' GROUP BY d ORDER BY d"
).result_rows
dates = [datetime.datetime.fromtimestamp(r[0], datetime.UTC).date() for r in rows]
hi = [r[1] for r in rows]; lo = [r[2] for r in rows]; px = [r[3] for r in rows]


def atr(i, n=20):
    if i < n: return None
    return sum(max(hi[j]-lo[j], abs(hi[j]-px[j-1]), abs(lo[j]-px[j-1])) for j in range(i-n+1, i+1)) / n


def er(i, n=50):
    if i < n: return None
    path = sum(abs(px[j]-px[j-1]) for j in range(i-n+1, i+1))
    return abs(px[i]-px[i-n]) / path if path else 0.0


reg = {}
for i, d in enumerate(dates):
    a = atr(i)
    reg[d] = (er(i), a)

print("=== распределение ER по годам (доля дней)")
for y in range(2019, 2027):
    ds = [v[0] for d, v in reg.items() if d.year == y and v[0] is not None]
    if not ds: continue
    print(f"  {y}: дней {len(ds):3d} ER_med={st.median(ds):.2f} доля>=0.3 {sum(1 for x in ds if x>=0.3)/len(ds)*100:4.0f}%  >=0.4 {sum(1 for x in ds if x>=0.4)/len(ds)*100:4.0f}%")

grid = json.load(open("results/gold_grid_years.json"))
tr = [(datetime.datetime.fromtimestamp(t["ts"], datetime.UTC).date(), t["pips"]) for t in grid[CFG]]


def show(label, keep):
    per = defaultdict(list)
    for d, p in tr:
        if keep(d): per[d.year].append(p)
    allp = [p for v in per.values() for p in v]
    if not allp: return
    n = len(allp); m = sum(allp)/n; sd = st.stdev(allp) if n > 1 else 0
    t = m/(sd/n**0.5) if sd else 0
    sm = " ".join(f"{y}:{len(v)}/{sum(v):+.0f}" for y, v in sorted(per.items()))
    print(f"{label:22s} n={n:4d} сумма={sum(allp):+7.0f} {m:+6.1f}/сд t={t:5.2f} WR={sum(1 for v in allp if v>0)/n*100:3.0f}% | {sm}")


print(f"\n=== {CFG}")
show("без фильтра", lambda d: True)
for thr in (0.2, 0.25, 0.3, 0.35, 0.4, 0.45):
    show(f"ER>={thr}", lambda d, th=thr: (reg.get(d, (0, 0))[0] or 0) >= th)
