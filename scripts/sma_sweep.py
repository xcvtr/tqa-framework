#!/usr/bin/env python3
"""Подбор периодов SMA per ticker для trend_sma на форексе.

Схема: IS = 2025 год (сетка fast/slow/ma_type), OOS = 2026 (проверка ТОЛЬКО
отобранных конфигов, без повторного перебора). 1 лот, фикс. сайзинг; издержки
внутри BrokerSim (спред 1.2 пп круг + комиссия 3.5 USD/лот/нога).

  PYTHONPATH=. python scripts/sma_sweep.py --tickers eurusd,gbpusd
  PYTHONPATH=. python scripts/sma_sweep.py --top 3         # + OOS по топ-3

Выход: results/sma_sweep_<year>.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime as _dt

import numpy as np

sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.broker import BrokerSim                       # noqa: E402
from strategies.trend_sma.data_provider import load_ticker_bars        # noqa: E402
from strategies.trend_sma.engine import Config, MarketData, State, force_close, mtm_mdd, run_days  # noqa: E402
from strategies.trend_sma.self_tester import load_specs                # noqa: E402
from tqa_f.data.forex_specs import SPECS, rate_symbols                 # noqa: E402

log = logging.getLogger('sweep')
_RATE_KEY = {'usdjpy': 'JPY', 'usdcad': 'CAD', 'usdchf': 'CHF',
             'audusd': 'AUD', 'gbpusd': 'GBP', 'eurusd': 'EUR'}


def build_md(ticker: str, load_start: str = '2019-10-01', end: str = '2026-11-17') -> MarketData:
    md = MarketData()
    bars = load_ticker_bars(ticker, load_start, end)
    if not bars:
        raise SystemExit(f'нет данных: {ticker}')
    md.add_ticker(ticker, bars)
    md.finalize()
    return md


def load_rates(load_start: str = '2019-10-01', end: str = '2026-11-17') -> dict:
    out = {}
    for rs in rate_symbols():
        rb = load_ticker_bars(rs, load_start, end)
        if rb:
            out[_RATE_KEY[rs]] = (np.array([int(b['ts'].timestamp()) for b in rb], dtype='int64'),
                                  np.array([b['prc'] for b in rb], dtype='float64'))
    return out


def run_cfg(md: MarketData, ticker: str, fast: int, slow: int, ma: str,
            dates: list[str], rates: dict, eq0=200_000.0, sl=1.5, arm=2.0, trail=1.5) -> dict:
    specs = load_specs([ticker])
    cfg = Config(start=dates[0], eq_cap=eq0, specs=specs, fixed_lots=1.0, rates=rates,
                 params_by_ticker={ticker: {'sma_fast': fast, 'sma_slow': slow, 'ma_type': ma}},
                 trailing_params={'trail_mode': 'trend_sma', 'stop_loss': sl,
                                  'activation': arm, 'trail': trail, 'oi_pyramid_max': 0},
                 broker=BrokerSim(commission=3.5, slippage_in=0.6, slippage_out=0.6))
    st = State(eq0)
    run_days(md, st, cfg, dates)
    force_close(md, st, cfg)
    tr = st.closed_trades
    pip_val = SPECS[ticker]['contract'] * SPECS[ticker]['point']
    pips = sum(t['pnl_quote'] / pip_val for t in tr) if tr else 0.0
    usd = sum(t['pnl'] for t in tr)
    wins = sum(1 for t in tr if t['pnl'] > 0)
    return {'fast': fast, 'slow': slow, 'ma': ma, 'sl': sl, 'arm': arm, 'trail': trail,
            'n': len(tr), 'pips': round(pips, 1),
            'usd': round(usd, 1), 'wr': round(wins / len(tr) * 100, 1) if tr else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tickers', default=','.join(SPECS))
    ap.add_argument('--fast', default='10,20,35,50')
    ap.add_argument('--slow', default='60,100,150,200')
    ap.add_argument('--ma', default='sma,ema')
    ap.add_argument('--sl', default='1.5', help='stop_loss %% (список через запятую)')
    ap.add_argument('--arm', default='2.0', help='activation %%')
    ap.add_argument('--trail', default='1.5', help='trail %%')
    ap.add_argument('--year', default='2025', help='IS-год (метка для вывода)')
    ap.add_argument('--is-start', default='2025-01-01')
    ap.add_argument('--is-end', default='2025-12-31')
    ap.add_argument('--top', type=int, default=3, help='сколько лучших гонять на OOS')
    ap.add_argument('--oos-year', default='2026')
    ap.add_argument('--oos-start', default='2026-01-01')
    ap.add_argument('--oos-end', default='2026-11-30')
    ap.add_argument('--min-n', type=int, default=20, help='гейт: минимум сделок на IS')
    ap.add_argument('--load-start', default='2019-10-01')
    ap.add_argument('--oos-all', action='store_true', help='прогнать OOS по всей сетке')
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]
    grid = [(f, s, m, sl, arm, tr) for f in (int(x) for x in args.fast.split(','))
            for s in (int(x) for x in args.slow.split(',')) for m in args.ma.split(',')
            for sl in (float(x) for x in args.sl.split(','))
            for arm in (float(x) for x in args.arm.split(','))
            for tr in (float(x) for x in args.trail.split(','))]
    rates = load_rates(args.load_start, args.oos_end)
    out = {}
    for tk in tickers:
        md = build_md(tk, args.load_start, args.oos_end)
        is_dates = [d for d in md.dates if args.is_start <= d <= args.is_end]
        oos_dates = [d for d in md.dates if args.oos_start <= d <= args.oos_end]
        rows = [run_cfg(md, tk, f, s, m, is_dates, rates, sl=sl, arm=arm, trail=tr)
                for f, s, m, sl, arm, tr in grid]
        # ранг по пп на сделку: сумма пп на малой выборке вознаграждает шум
        rows.sort(key=lambda r: (r['pips'] / r['n'] if r['n'] else -1e9), reverse=True)
        best = [r for r in rows if r['n'] >= args.min_n][:args.top]
        for r in (rows if args.oos_all else best):
            r['oos'] = run_cfg(md, tk, r['fast'], r['slow'], r['ma'], oos_dates, rates,
                               sl=r['sl'], arm=r['arm'], trail=r['trail'])
        out[tk] = {'is_year': args.year, 'grid_n': len(rows), 'all': rows, 'best': best}
        print(f"{tk:8} IS{args.year} лучший: {best[0]['fast']}/{best[0]['slow']}{best[0]['ma']}"
              f" sl{best[0]['sl']} tr{best[0]['trail']} n={best[0]['n']} {best[0]['pips']:+.0f}пп"
              f" | OOS {best[0]['oos']['pips']:+.0f}пп n={best[0]['oos']['n']}"
              if best else f'{tk}: нет конфигов с n>=min_n', flush=True)
        del md
    path = args.out or f'results/sma_sweep_{args.year}.json'
    with open(path, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('saved', path)


if __name__ == '__main__':
    main()
