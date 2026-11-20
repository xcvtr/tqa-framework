#!/usr/bin/env python3
"""Батарея фильтров боковика на золоте. Критерий годности: на IS 2019-2024
доллар на сделку должен РАСТИ (не просто меньше сделок), иначе фильтр бесполезен.

Все метрики каузальны: считаются по дневным close/high/low закрытых дней,
включая день входа.

  CHOP    — классический индекс Чоппа: 100*log10(sum TR/(maxH-minL))/log10(N).
            Высокий (~60+) = пила/боковик, низкий (<38) = тренд.
  VR(q)   — variance ratio Ло-Маккинли: Var(q-дневный ход)/(q*Var(дневной)). >1 тренд.
  R2      — квадрат корреляции лог-цены с номером дня за 60 дней (прямизна тренда).
  HURST   — показатель Хёрста по R/S за 100 дней: <0.5 возврат к среднему.
  AC1     — автокорреляция дневных доходностей за 60 дней: <0 = возврат,
            >0 = моментум.
  TRENDZ  — |C[t]-C[t-n]| / (sigma_дневная*sqrt(n)) за n=50: насколько ход
            больше случайного шума.
  SIG     — sigma20/sigma100: расширение волатильности.
  DONCH   — ширина канала 60 дней / ATR20: широкий канал = тренд.
"""
import json
import math
import datetime
import statistics as st
from collections import defaultdict

from strategies.trend_sma.data_provider import ch_client

N_ER, N_REG = 50, 120
c = ch_client()
rows = c.query(
    "SELECT toUnixTimestamp(toDate(time)) d, max(high) h, min(low) l, argMax(close, time) px "
    "FROM forex.bars WHERE symbol='xauusd' AND time>='2017-06-01' GROUP BY d ORDER BY d"
).result_rows
dates = [datetime.datetime.fromtimestamp(r[0], datetime.UTC).date() for r in rows]
hi = [r[1] for r in rows]; lo = [r[2] for r in rows]; px = [r[3] for r in rows]
n_all = len(dates)


def tr(i):
    if i == 0:
        return hi[i] - lo[i]
    return max(hi[i] - lo[i], abs(hi[i] - px[i - 1]), abs(lo[i] - px[i - 1]))


def chop(i, n=14):
    if i < n:
        return None
    s = sum(tr(j) for j in range(i - n + 1, i + 1))
    rng = max(hi[i - n + 1:i + 1]) - min(lo[i - n + 1:i + 1])
    if rng <= 0 or s <= 0:
        return None
    return 100 * math.log10(s / rng) / math.log10(n)


def vr(i, q=5, win=120):
    if i < win:
        return None
    rets = [math.log(px[j] / px[j - 1]) for j in range(i - win + 1, i + 1)]
    v1 = st.pvariance(rets)
    if v1 <= 0:
        return None
    qr = [sum(rets[k:k + q]) for k in range(0, len(rets) - q + 1, 1)]
    vq = st.pvariance(qr)
    return vq / (q * v1) if v1 else None


def r2(i, n=60):
    if i < n:
        return None
    ys = [math.log(px[j]) for j in range(i - n + 1, i + 1)]
    xs = list(range(len(ys)))
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return (sxy * sxy) / (sxx * syy)


def hurst(i, n=100):
    if i < n:
        return None
    seg = [math.log(px[j] / px[j - 1]) for j in range(i - n + 1, i + 1)]

    def rs(x):
        m = sum(x) / len(x)
        dev = [v - m for v in x]
        cum, cc = 0.0, []
        for v in dev:
            cum += v
            cc.append(cum)
        r = max(cc) - min(cc)
        s = st.pstdev(x)
        return r / s if s > 0 else None

    pts = []
    for size in (10, 20, 40):
        vals = [rs(seg[k:k + size]) for k in range(0, len(seg) - size + 1, size)]
        vals = [v for v in vals if v]
        if vals:
            pts.append((math.log(size), math.log(st.mean(vals))))
    if len(pts) < 2:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxy = sum((a - mx) * (b - my) for a, b in pts)
    sxx = sum((a - mx) ** 2 for a, b in pts)
    return sxy / sxx if sxx else None


def ac1(i, n=60):
    if i < n:
        return None
    r = [math.log(px[j] / px[j - 1]) for j in range(i - n + 1, i + 1)]
    m = sum(r) / len(r)
    num = sum((r[k] - m) * (r[k + 1] - m) for k in range(len(r) - 1))
    den = sum((v - m) ** 2 for v in r)
    return num / den if den else None


def trendz(i, n=50):
    if i < n:
        return None
    r = [math.log(px[j] / px[j - 1]) for j in range(i - n + 1, i + 1)]
    sd = st.pstdev(r)
    if sd <= 0:
        return None
    return abs(px[i] - px[i - n]) / (sd * math.sqrt(n) * px[i])


def sig_ratio(i):
    if i < 100:
        return None
    r = [math.log(px[j] / px[j - 1]) for j in range(i - 99, i + 1)]
    return st.pstdev(r[-20:]) / st.pstdev(r) if st.pstdev(r) else None


def donch(i, n=60):
    if i < max(n, 20):
        return None
    a = sum(tr(j) for j in range(i - 19, i + 1)) / 20
    return (max(hi[i - n + 1:i + 1]) - min(lo[i - n + 1:i + 1])) / a if a else None


REG = {}
for i, d in enumerate(dates):
    REG[d] = {"CHOP": chop(i), "VR5": vr(i, 5), "VR10": vr(i, 10), "R2": r2(i),
              "HURST": hurst(i), "AC1": ac1(i), "TRENDZ": trendz(i),
              "SIG": sig_ratio(i), "DONCH": donch(i)}

tr_by_day = defaultdict(list)
for t in json.load(open("results/er_0.json")):
    ts = t.get("entry_i") or t.get("ts")
    tr_by_day[datetime.datetime.fromtimestamp(ts, datetime.UTC).date()].append(t["pnl"])

all_days = sorted(tr_by_day)
is_t = [(d, p) for d in all_days for p in tr_by_day[d] if d.year < 2025]
oos_t = [(d, p) for d in all_days for p in tr_by_day[d] if d.year >= 2025]


def stat(ts_):
    n = len(ts_)
    if not n:
        return None
    s = sum(p for _, p in ts_)
    return n, s, s / n


b_is, b_oos = stat(is_t), stat(oos_t)
print(f"БАЗА   IS2019-24: {b_is[0]} сд {b_is[1]:+.0f}$ {b_is[2]:+.2f}$/сд | "
      f"OOS2025-26: {b_oos[0]} сд {b_oos[1]:+.0f}$ {b_oos[2]:+.2f}$/сд")

print(f"\n{'метрика':8s} {'правило':22s} {'IS n':>5s} {'IS $/сд':>9s} {'Δ%':>6s}  {'OOS n':>6s} {'OOS $/сд':>9s}")
rows_out = []
for m in ("CHOP", "VR5", "VR10", "R2", "HURST", "AC1", "TRENDZ", "SIG", "DONCH"):
    vals = [REG[d][m] for d in all_days if REG.get(d, {}).get(m) is not None]
    if not vals:
        continue
    qs = sorted(vals)
    cands = []
    # для «тренд-положительных» метрик оставляем верхний хвост, для CHOP — низ
    fracs = (0.5, 0.6, 0.7, 0.8)
    for fr in fracs:
        thr_hi = qs[int(len(qs) * fr) - 1]
        thr_lo = qs[int(len(qs) * (1 - fr))]
        cands.append((f">= {thr_hi:.3f} (верх {int(fr*100)}%)", lambda d, m=m, t=thr_hi: (REG.get(d, {}).get(m) or -9e9) >= t))
        cands.append((f"<= {thr_lo:.3f} (низ {int(fr*100)}%)", lambda d, m=m, t=thr_lo: (REG.get(d, {}).get(m) if REG.get(d, {}).get(m) is not None else 9e9) <= t))
    best = None
    for label, f in cands:
        s_is = stat([(d, p) for d, p in is_t if f(d)])
        if not s_is or s_is[0] < 20:
            continue
        if best is None or s_is[2] > best[1][2]:
            best = (label, s_is)
    if not best:
        continue
    label, s_is = best
    s_oos = stat([(d, p) for d, p in oos_t if dict(cands)[label](d)]) if label in dict(cands) else None
    delta = (s_is[2] / b_is[2] - 1) * 100 if b_is[2] else 0
    rows_out.append((delta, m, label, s_is, s_oos))
    print(f"{m:8s} {label:22s} {s_is[0]:5d} {s_is[2]:+9.2f} {delta:+6.0f}%  "
          f"{s_oos[0] if s_oos else 0:6d} {s_oos[2] if s_oos else 0:+9.2f}")

print("\nлучшие по IS (Δ$/сд к базе):")
for delta, m, label, s_is, s_oos in sorted(rows_out, reverse=True)[:4]:
    print(f"  {m:8s} {label:22s} IS {s_is[2]:+.2f}$/сд (n={s_is[0]})  OOS {s_oos[2] if s_oos else 0:+.2f}$/сд (n={s_oos[0] if s_oos else 0})")
