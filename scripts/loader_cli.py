#!/usr/bin/env python3
"""Loader CLI (отдельный контейнер): roll ← continuous.

  python scripts/loader_cli.py --once
  python scripts/loader_cli.py --loop 300          # живой режим контейнера
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from tqa_f.data.roll_loader import CANON, extend

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('loader')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tickers', default='')
    ap.add_argument('--loop', type=int, default=0, help='секунд между прогонами (0 = один прогон)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    tickers = [t for t in a.tickers.split(',') if t] or None

    while True:
        try:
            res = extend(tickers, dry_run=a.dry_run)
            log.info('итого: %s', {k: v for k, v in res.items() if v})
        except Exception:
            log.exception('loader упал, повтор через %ss', a.loop or 60)
            if not a.loop:
                sys.exit(1)
        if not a.loop:
            return
        time.sleep(a.loop)


if __name__ == '__main__':
    main()
