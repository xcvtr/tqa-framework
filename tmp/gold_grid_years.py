#!/usr/bin/env python3
"""Сетка xauusd по всей истории за ОДИН прогон: данные грузятся один раз,
по каждому конфигу пишутся сделки с датами — чтобы потом делать walk-forward по годам.

PYTHONPATH=. python tmp/gold_grid_years.py
Выход: results/gold_grid_years.json
"""
import json
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

from datetime import datetime as _dt
from strategies.trend_sma.broker import BrokerSim
from strategies.trend_sma.data_provider import load_ticker_bars
from strategies.trend_sma.engine import Config, MarketData, State, force_close, run_days
from strategies.trend_sma.self_tester import load_specs
from tqa_f.data.forex_specs import SPECS

TICKER = 'xauusd'
START, END = '2019-01-01', '2026-11-17'
FASTS, SLOWS, MAS = (10, 20, 35, 50), (60, 100, 150, 200), ('sma', 'ema')

pip_val = SPECS[TICKER]['contract'] * SPECS[TICKER]['pip']
md = MarketData()
md.add_ticker(TICKER, load_ticker_bars(TICKER, START, END))
md.finalize()
dates = md.dates
print(f'md: {len(dates)} дней {dates[0]}..{dates[-1]}', flush=True)

out = {}
for f in FASTS:
    for s in SLOWS:
        for m in MAS:
            cfg = Config(start=START, eq_cap=200_000.0, specs=load_specs([TICKER]), fixed_lots=1.0,
                         params_by_ticker={TICKER: {'sma_fast': f, 'sma_slow': s, 'ma_type': m}},
                         trailing_params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                          'activation': 2.0, 'trail': 1.5, 'oi_pyramid_max': 0},
                         broker=BrokerSim(commission=3.5, slippage_in=0.6, slippage_out=0.6))
            st = State(200_000.0)
            run_days(md, st, cfg, dates)
            force_close(md, st, cfg)
            trades = [{'ts': int(t['_entry_i'] if '_entry_i' in t else t.get('entry_i', 0)),
                       'pips': round(t['pnl_quote'] / pip_val, 2)} for t in st.closed_trades]
            out[f'{f}/{s}/{m}'] = trades
            tot = sum(t['pips'] for t in trades)
            print(f'{f}/{s}/{m}: n={len(trades)} пп={tot:+.0f}', flush=True)

json.dump(out, open('results/gold_grid_years.json', 'w'))
print('saved results/gold_grid_years.json', flush=True)
