#!/usr/bin/env python3
"""Position-layer CLI: ОДИН entrypoint на образ, режим выбирается флагом.

  --mode test  → бэктест по истории (self_tester)
  --mode paper → живой цикл по закрытым барам CH (live), с журналом в PG

Торговой логики здесь нет — только разбор аргументов и делегирование.
"""
import argparse
import logging
import sys


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='TQA-M position layer')
    ap.add_argument('--mode', choices=['paper', 'test'], default='test')
    ap.add_argument('--persist', choices=['pg', 'none'], default='none')
    ap.add_argument('--pg-schema', default='')   # paper по умолчанию пишет в tqa_f_paper
    ap.add_argument('--reset-state', action='store_true')
    ap.add_argument('--connector', choices=['mock', 'none', 'mt5'], default='none')
    ap.add_argument('--signals', choices=['local', 'pg'], default='local')
    ap.add_argument('--signals-schema', default='futures')
    ap.add_argument('--poll', type=int, default=20, help='сек между опросами CH (paper)')
    ap.add_argument('--save-every', type=int, default=300, help='сек между save state (paper)')
    ap.add_argument('--tickers', required=True)
    ap.add_argument('--start', default='2026-01-01')
    ap.add_argument('--end', default='2026-11-11')
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]

    if args.connector == 'mt5':
        raise NotImplementedError("connector mt5: ещё не реализован (mock/none)")

    if args.mode == 'test':
        from strategies.trend_sma.self_tester import main as bt_main
        argv = ['self_tester', '--tickers', ','.join(tickers), '--start', args.start,
                '--end', args.end, '--persist', args.persist,
                '--pg-schema', args.pg_schema or 'tqa_f_bt',
                '--connector', args.connector, '--signals', args.signals,
                '--signals-schema', args.signals_schema]
        if args.reset_state:
            argv.append('--reset-state')
        sys.argv = argv
        sys.exit(bt_main() or 0)

    from strategies.trend_sma import live
    # --start обязателен: раньше терялся, и папер молча стартовал «с сегодня»
    sys.argv = ['live', '--tickers', ','.join(tickers), '--paper', '--poll', str(args.poll),
                '--save-every', str(args.save_every), '--start', args.start,
                '--connector', args.connector, '--signals', args.signals,
                '--signals-schema', args.signals_schema,
                '--pg-schema', args.pg_schema or 'tqa_f_paper']
    live.main()


if __name__ == '__main__':
    main()
