#!/usr/bin/env python3
"""Валидация замороженных per-ticker конфигов trend_sma на 2019–2024.

Конфиги НЕ подбираются заново: берутся из results/sweep_is2425_*.json (лучший
по IS 2024–2025). Годы 2019–2024 гипотеза не видела — это честный длинный тест.

  PYTHONPATH=. python scripts/sma_validate.py

Выход: results/sma_validate.json + погодовая разбивка.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

sys.path.insert(0, '/home/user/projects/tqa-f/scripts')
from sma_sweep import build_md, load_rates, run_cfg  # noqa: E402

log = logging.getLogger('validate')


def frozen_configs(pattern: str) -> dict:
    out = {}
    for f in sorted(glob.glob(pattern)):
        d = json.load(open(f))
        for tk, v in d.items():
            if v.get('best'):
                b = v['best'][0]
                out[tk] = {'fast': b['fast'], 'slow': b['slow'], 'ma': b['ma'],
                           'sl': b.get('sl', 1.5), 'arm': b.get('arm', 2.0),
                           'trail': b.get('trail', 1.5)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pattern', default='results/sweep_is2425_*.json')
    ap.add_argument('--start', default='2020-01-01')
    ap.add_argument('--end', default='2024-12-31')
    ap.add_argument('--load-start', default='2019-01-01')
    ap.add_argument('--out', default='results/sma_validate.json')
    ap.add_argument('--tickers', default='', help='список через запятую; пусто = все из pattern')
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    cfgs = frozen_configs(args.pattern)
    if args.tickers:
        keep = {t.strip() for t in args.tickers.split(',') if t.strip()}
        cfgs = {k: v for k, v in cfgs.items() if k in keep}
    rates = load_rates(args.load_start, args.end)
    res, total = {}, 0.0
    for tk, c in sorted(cfgs.items()):
        md = build_md(tk, args.load_start, args.end)
        dates = [d for d in md.dates if args.start <= d <= args.end]
        r = run_cfg(md, tk, c['fast'], c['slow'], c['ma'], dates, rates,
                    sl=c['sl'], arm=c['arm'], trail=c['trail'])
        by_year = {}
        for y in sorted({d[:4] for d in dates}):
            dy = [d for d in dates if d[:4] == y]
            ry = run_cfg(md, tk, c['fast'], c['slow'], c['ma'], dy, rates,
                         sl=c['sl'], arm=c['arm'], trail=c['trail'])
            by_year[y] = [ry['n'], ry['pips']]
        r['by_year'] = by_year
        r['cfg'] = f"{c['fast']}/{c['slow']}{c['ma']}"
        res[tk] = r
        total += r['pips']
        print(f"{tk:8} {r['cfg']:>12} n={r['n']:3d} {r['pips']:+8.1f}пп "
              f"WR={r['wr']:5.1f}% | " + ' '.join(f"{y}:{v[1]:+.0f}({v[0]})"
                                                 for y, v in by_year.items()), flush=True)
        del md
    print(f"ИТОГО {total:+.1f} пп, пар в плюсе {sum(1 for r in res.values() if r['pips'] > 0)}/{len(res)}")
    with open(args.out, 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('saved', args.out)


if __name__ == '__main__':
    main()
