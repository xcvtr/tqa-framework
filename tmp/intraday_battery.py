#!/usr/bin/env python3
"""Внутридневные признаки боковика (независимые от дневной MA): батарея на золоте.
Критерий годности жёстче прежнего: должна расти t-статистика на сделку,
а не только средний результат.

PYTHONPATH=. python tmp/intraday_battery.py
"""
import json
import math
import sys
from datetime import datetime, UTC

sys.path.insert(0, '/home/user/projects/tqa-f')
from strategies.trend_sma.data_provider import load_ticker_bars

TK, START, END = 'xauusd', '2019-01-01', '2026-11-17'

bars = load_ticker_bars(TK, START, END)     # 5м? нет: M1. Группируем в 5м по времени.
print('баров:', len(bars), flush=True)

# ── дневные агрегаты из M1 ──
days = {}
for b in bars:
    d = b['ts'].strftime('%Y-%m-%d')
    o, h, l, c = b['opn'], b['hi'], b['lo'], b['prc']
    if d not in days:
        days[d] = {'o': o, 'h': h, 'l': l, 'c': c, 'sum_abs': 0.0, 'prev': None, 'moves': 0}
    g = days[d]
    g['h'] = max(g['h'], h); g['l'] = min(g['l'], l); g['c'] = c
    if g['prev'] is not None:
        g['sum_abs'] += abs(c - g['prev']); g['moves'] += 1
    g['prev'] = c

ds = sorted(days)
print('дней:', len(ds), flush=True)

# ── признаки по прошлым дням (каузально) ──
feat = {}
for i, d in enumerate(ds):
    if i < 61:
        continue
    row = {}
    w20 = [days[x] for x in ds[i - 20:i]]
    w60 = [days[x] for x in ds[i - 60:i]]
    # 1. внутридневная эффективность: тело дня / суммарный путь
    row['er_day'] = sum(abs(g['c'] - g['o']) / g['sum_abs'] for g in w20 if g['sum_abs'] > 0) / 20
    # 2. телесность: |C-O| / (H-L)
    row['body'] = sum(abs(g['c'] - g['o']) / (g['h'] - g['l']) for g in w20 if g['h'] > g['l']) / 20
    # 3. сжатие диапазона: средний диапазон 10 дней к 60 дням
    r10 = sum((g['h'] - g['l']) / g['c'] for g in (days[x] for x in ds[i - 10:i])) / 10
    r60 = sum((g['h'] - g['l']) / g['c'] for g in w60) / 60
    row['rng'] = r10 / r60 if r60 else None
    # 4. ложные пробои: пробил экстремум предыдущих 20 дней, но закрылся внутри
    fb = 0
    for j in range(i - 20, i):
        g = days[ds[j]]
        ph = max(days[x]['h'] for x in ds[j - 20:j])
        pl = min(days[x]['l'] for x in ds[j - 20:j])
        if (g['h'] > ph or g['l'] < pl) and pl <= g['c'] <= ph:
            fb += 1
    row['fb'] = fb / 20
    # 5. продолжение дня: знак дня совпал со знаком дня к прошлому close
    row['pers'] = sum(1 for k in range(i - 20, i)
                      if (days[ds[k]]['c'] - days[ds[k]]['o'])
                      * (days[ds[k]]['c'] - days[ds[k - 1]]['c']) > 0) / 20
    # 6. доля дней, закрывшихся в верхней/нижней трети диапазона (направленность закрытий)
    row['close_pos'] = sum(abs((g['c'] - g['l']) / (g['h'] - g['l']) - 0.5) for g in w20 if g['h'] > g['l']) / 20
    feat[d] = row

# ── сделки ──
trades = json.load(open('results/er_0.json'))
rows = []
for t in trades:
    ts = t.get('entry_i') or t.get('ts') or 0
    d = datetime.fromtimestamp(int(ts), UTC).strftime('%Y-%m-%d')
    if d in feat:
        rows.append({'d': d, 'pnl': t['pnl'], 'y': int(d[:4]), 'f': feat[d]})
print('сделок с признаками:', len(rows), flush=True)


def stat(xs):
    n = len(xs)
    if n < 5:
        return (n, 0.0, 0.0)
    m = sum(xs) / n
    sd = (sum((v - m) ** 2 for v in xs) / (n - 1)) ** 0.5
    return (n, m, m / (sd / n ** 0.5) if sd else 0.0)


def show(tag, xs):
    n, m, t = stat(xs)
    print(f'  {tag:22s} n={n:3d} mean={m:+7.2f}$ t={t:+5.2f}', flush=True)


base_is = [r['pnl'] for r in rows if r['y'] <= 2024]
base_oos = [r['pnl'] for r in rows if r['y'] >= 2025]
print('\nБАЗА', flush=True)
show('IS 2019-2024', base_is)
show('OOS 2025-2026', base_oos)

for name in ('er_day', 'body', 'rng', 'fb', 'pers', 'close_pos'):
    vals = sorted(r['f'][name] for r in rows if r['f'][name] is not None)
    if not vals:
        continue
    print(f'\n{name}: медиана={vals[len(vals)//2]:.3f} '
          f'q10={vals[len(vals)//10]:.3f} q90={vals[9*len(vals)//10]:.3f}', flush=True)
    for q in (0.25, 0.35, 0.5):
        thr = vals[int(q * len(vals))]
        for op in ('<=', '>='):
            sel = [r for r in rows if r['f'][name] is not None
                   and ((r['f'][name] <= thr) if op == '<=' else (r['f'][name] >= thr))]
            if len(sel) < 20:
                continue
            show(f'{name}{op}{thr:.3f}', [r['pnl'] for r in sel])

print('\n=== КАНДИДАТЫ: IS / OOS ===', flush=True)
can = [('fb', '>=', 0.05), ('fb', '>=', 0.10), ('body', '>=', 0.44),
       ('pers', '>=', 0.95), ('close_pos', '<=', 0.252)]
show('БАЗА IS', base_is)
show('БАЗА OOS', base_oos)
for name, op, thr in can:
    sel = [r for r in rows if r['f'][name] is not None
           and ((r['f'][name] <= thr) if op == '<=' else (r['f'][name] >= thr))]
    i_ = [r['pnl'] for r in sel if r['y'] <= 2024]
    o_ = [r['pnl'] for r in sel if r['y'] >= 2025]
    print(f'{name}{op}{thr}', flush=True)
    show('  IS', i_)
    show('  OOS', o_)

print('\n=== КОМБИНАЦИИ ===', flush=True)
def combo(pred, tag):
    sel = [r for r in rows if pred(r['f'])]
    show(tag + ' ALL', [r['pnl'] for r in sel])
    show('  IS', [r['pnl'] for r in sel if r['y'] <= 2024])
    show('  OOS', [r['pnl'] for r in sel if r['y'] >= 2025])

combo(lambda f: f['body'] is not None and f['fb'] is not None and f['body'] >= 0.44 and f['fb'] >= 0.05, 'body+fb')
combo(lambda f: f['body'] is not None and f['pers'] is not None and f['body'] >= 0.44 and f['pers'] >= 0.95, 'body+pers')
combo(lambda f: f['body'] is not None and f['rng'] is not None and f['body'] >= 0.44 and f['rng'] <= 1.0, 'body+rng')
