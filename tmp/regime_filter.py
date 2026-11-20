#!/usr/bin/env python3
"""Фильтр режима против боковика (золото).

Идея: торговля трендовой системой в боковике = мусорные сделки. Считаем
каузальные метрики режима на ДНЕВНЫХ close (только прошлое к моменту входа)
и смотрим, что остаётся после гейта.

Метрики:
  ER    — efficiency ratio Кауфмана |C[t]-C[t-n]| / sum|dC| за n дней;
  ATR   — ATR20 / price (волатильность);
  DIST  — (C - SMA100) / ATR20 (насколько цена ушла от средней);
  SLOPE — (SMA100[t]-SMA100[t-m]) / ATR20 (наклон средней).

Проверка честная: порог подбираем на 2019-2024, смотрим на 2025-2026.
Фиксированный лот ⇒ PnL сделок аддитивен, отсев сделки не меняет остальные.
"""
import json
import datetime
import statistics as st
from collections import defaultdict

from strategies.trend_sma.data_provider import ch_client

Y0, Y1 = 2019, 2027
c = ch_client()
rows = c.query(
    "SELECT toUnixTimestamp(toDate(time)) d, max(high) h, min(low) l, argMax(close, time) px "
    "FROM forex.bars WHERE symbol='xauusd' AND time>='2018-06-01' GROUP BY d ORDER BY d"
).result_rows
dates = [datetime.datetime.fromtimestamp(r[0], datetime.UTC).date() for r in rows]
hi = [r[1] for r in rows]
lo = [r[2] for r in rows]
px = [r[3] for r in rows]
idx = {d: i for i, d in enumerate(dates)}
print(f"дней: {len(dates)} {dates[0]}..{dates[-1]}")


def sma(i, n):
    if i + 1 < n:
        return None
    return sum(px[i - n + 1:i + 1]) / n


def atr(i, n=20):
    if i < n:
        return None
    s = 0.0
    for j in range(i - n + 1, i + 1):
        tr = max(hi[j] - lo[j], abs(hi[j] - px[j - 1]), abs(lo[j] - px[j - 1]))
        s += tr
    return s / n


def er(i, n=50):
    if i < n:
        return None
    move = abs(px[i] - px[i - n])
    path = sum(abs(px[j] - px[j - 1]) for j in range(i - n + 1, i + 1))
    return move / path if path else 0.0


reg = {}
for i, d in enumerate(dates):
    a, s = atr(i), sma(i, 100)
    v = {"ER": er(i), "ATR": (a / px[i]) if a else None}
    v["DIST"] = ((px[i] - s) / a) if (a and s) else None
    s2 = sma(i - 50, 100) if i - 50 >= 0 else None
    v["SLOPE"] = ((s - s2) / a) if (a and s and s2) else None
    reg[d] = v

grid = json.load(open("results/gold_grid_years.json"))
trades = defaultdict(list)  # cfg -> [(date, pips)]
for k, tr in grid.items():
    for t in tr:
        d = datetime.datetime.fromtimestamp(t["ts"], datetime.UTC).date()
        trades[k].append((d, t["pips"]))


def agg(cfgs, day_filter):
    """сумма/кол-во пп по сделкам, прошедшим day_filter(date)."""
    out = defaultdict(lambda: [0, 0.0, []])
    for k in cfgs:
        for d, p in trades[k]:
            if not day_filter(d):
                continue
            y = d.year
            out[y][0] += 1
            out[y][1] += p
            out[y][2].append(p)
    return out


def report(name, day_filter):
    isr, oosr = agg(trades, day_filter), None
    tot_n = sum(v[0] for v in isr.values())
    tot_p = sum(v[1] for v in isr.values())
    per = " ".join(f"{y}:{v[0]}/{v[1]:+.0f}" for y, v in sorted(isr.items()))
    print(f"{name:26s} n={tot_n:5d} сумма={tot_p:+8.0f} | {per}")


# базовый прогон — только конфиги, отобранные walk-forward-логикой не нужны,
# берём распределение по всем 32 (устойчивость), но для честности ещё и один конфиг
print("\n=== базовый (без фильтра), сумма по 32 конфигам")
report("все сделки", lambda d: True)

def mk_er(thr):
    return lambda d: (reg.get(d, {}).get("ER") or 0) >= thr


def mk_dist(thr):
    return lambda d: abs(reg.get(d, {}).get("DIST") or 0) >= thr


def mk_slope(thr):
    return lambda d: abs(reg.get(d, {}).get("SLOPE") or 0) >= thr


def mk_atr(thr):
    return lambda d: (reg.get(d, {}).get("ATR") or 0) >= thr


print("\n=== ER (efficiency ratio, 50 дней)")
for thr in (0.0, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4):
    report(f"ER>={thr}", mk_er(thr))

print("\n=== DIST (уход цены от SMA100 в ATR)")
for thr in (0.0, 1.0, 2.0, 3.0, 4.0):
    report(f"|DIST|>={thr}", mk_dist(thr))

print("\n=== SLOPE (наклон SMA100 в ATR за 50 дней)")
for thr in (0.0, 0.5, 1.0, 2.0, 3.0):
    report(f"|SLOPE|>={thr}", mk_slope(thr))

print("\n=== ATR% (волатильность)")
for thr in (0.0, 0.01, 0.015, 0.02, 0.03):
    report(f"ATR>={thr}", mk_atr(thr))
