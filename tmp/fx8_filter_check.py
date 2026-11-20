#!/usr/bin/env python3
"""Проверка фильтров боковика на 12 парах за 8 лет. По одной паре в памяти:
база + постфактум-фильтрация (лот фиксированный, поэтому корректно).
Выход: results/fx8_filter_check.json

PYTHONPATH=. python tmp/fx8_filter_check.py
"""
import json
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

from datetime import datetime as _dt
from strategies.trend_sma.broker import BrokerSim
from strategies.trend_sma.data_provider import load_ticker_bars
from strategies.trend_sma.engine import Config, MarketData, State, force_close, regime_series, run_days
from strategies.trend_sma.self_tester import load_specs
from tqa_f.data.forex_specs import SPECS

START, END = '2019-01-01', '2026-11-17'
CFG = {
    'audjpy': (10, 100), 'audusd': (10, 60), 'euraud': (35, 150), 'eurgbp': (10, 60),
    'eurjpy': (20, 200), 'eurusd': (10, 200), 'gbpjpy': (20, 200), 'gbpusd': (10, 100),
    'usdcad': (20, 60), 'usdchf': (10, 100), 'usdjpy': (10, 100), 'xauusd': (50, 60),
}
RULES = [('r2', '<=', 0.40), ('vr5', '>=', 1.05), ('sig', '<=', 0.81),
         ('er', '>=', 0.20), ('chop', '<=', 48.0)]

out = {}
tot = {('base'): [0, 0.0]}
for r in RULES:
    tot[f'{r[0]}{r[1]}{r[2]}'] = [0, 0.0]

for tk, (f, s) in CFG.items():
    md = MarketData()
    md.add_ticker(tk, load_ticker_bars(tk, START, END))
    md.finalize()
    dates = md.dates
    pip = SPECS[tk]['contract'] * SPECS[tk]['pip']
    cfg = Config(start=START, eq_cap=200_000.0, specs=load_specs([tk]), fixed_lots=0.01,
                 params_by_ticker={tk: {'sma_fast': f, 'sma_slow': s, 'ma_type': 'sma'}},
                 trailing_params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                  'activation': 2.0, 'trail': 1.5, 'oi_pyramid_max': 0},
                 broker=BrokerSim(commission=SPECS[tk].get('fee', 0.0), slippage_in=0.6, slippage_out=0.6))
    st = State(200_000.0)
    run_days(md, st, cfg, dates)
    force_close(md, st, cfg)

    # дневные ряды для метрик режима (те же, что в движке)
    ds = sorted(md.day5[tk])
    cl, hi, lo = [], [], []
    for d in ds:
        a, b = md.day5[tk][d]
        cl.append(float(md.b5_p[tk][b - 1]))
        hi.append(float(max(md.b5_h[tk][a:b])))
        lo.append(float(min(md.b5_l[tk][a:b])))
    ser = {name: dict(zip(ds, regime_series(name, cl, hi, lo))) for name, _, _ in RULES}

    trades = []
    for t in st.closed_trades:
        i = t.get('_entry_i', t.get('entry_i', 0))
        ts = int(i)
        d = _dt.utcfromtimestamp(ts).strftime('%Y-%m-%d') if ts > 1e9 else None
        if d is None:  # индекс, не timestamp
            d = dates[i] if 0 <= i < len(dates) else None
        trades.append({'d': d, 'pp': t['pnl_quote'] / pip})

    base_n, base_pp = len(trades), sum(t['pp'] for t in trades)
    tot['base'][0] += base_n
    tot['base'][1] += base_pp
    row = {'n': base_n, 'pips': round(base_pp, 1), 'rules': {}}
    for name, op, thr in RULES:
        keep = []
        for t in trades:
            v = ser[name].get(t['d'])
            if v is None:
                continue
            if (v >= thr) if op == '>=' else (v <= thr):
                keep.append(t)
        k = f'{name}{op}{thr}'
        n, pp = len(keep), sum(t['pp'] for t in keep)
        tot[k][0] += n
        tot[k][1] += pp
        row['rules'][k] = {'n': n, 'pips': round(pp, 1)}
    out[tk] = row
    print(f"{tk}: base n={base_n} пп={base_pp:+.0f} | " +
          ' '.join(f"{k} n={v['n']} {v['pips']:+.0f}" for k, v in row['rules'].items()), flush=True)
    del md, st

json.dump(out, open('results/fx8_filter_check.json', 'w'))
print('\n=== ИТОГО 12 пар, 2019-2026 ===', flush=True)
bn, bp = tot['base']
print(f"база: n={bn} пп={bp:+.0f} ({bp/bn:+.2f} пп/сд)", flush=True)
for k, (n, pp) in tot.items():
    if k == 'base':
        continue
    print(f"{k}: n={n} пп={pp:+.0f} ({pp/n if n else 0:+.2f} пп/сд) сохранено {100*n/bn:.0f}% сделок", flush=True)
