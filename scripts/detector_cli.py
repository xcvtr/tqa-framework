#!/usr/bin/env python3
"""Detector CLI (образ детектора): check | scan.

Детектор читает ТОЛЬКО CH и не импортирует исполнение (broker/position_manager).
scan считает сигналы ровно на том же входе, что и движок: 149 закрытых дневных
баров + формирующийся первый 5м-бар дня (as-of), затем пишет их в PG.

  python scripts/detector_cli.py check
  python scripts/detector_cli.py scan --tickers MN,SG --as-of 2026-11-13
  python scripts/detector_cli.py scan --tickers MN --as-of 2026-11-13 --dry-run
"""
import argparse
import sys

DETECT_CONFIG = {'sma_fast': 20, 'sma_slow': 60, 'ma_type': 'ema', 'window_days': 150}
WARMUP_DAYS = 150


def check_self_sufficiency():
    try:
        from strategies.trend_sma.detect import detect
        if detect([], {}) != []:
            print("ERROR: detect([]) should return []")
            sys.exit(1)
        for mod in ('psycopg2', 'clickhouse_connect'):
            if mod in sys.modules:
                print(f"ERROR: expensive module {mod} was imported")
                sys.exit(1)
        for mod in ('strategies.trend_sma.broker', 'strategies.trend_sma.position_manager'):
            if mod in sys.modules:
                print(f"ERROR: detector imported executor layer: {mod}")
                sys.exit(1)
        print("Self-sufficiency check passed")
        return True
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: Self-sufficiency check failed: {e}")
        sys.exit(1)


def _daily_window(bars, as_of: str, keep: int = 149):
    """149 закрытых дневных баров до as_of + формирующийся первый 5м-бар дня as_of."""
    from tqa_f.data.bars import group_days, utc_date, resample5

    ts5, o5, h5, l5, p5, v5 = resample5(bars)
    day5 = group_days(ts5)
    days = sorted(d for d in day5 if d < as_of)[-keep:]
    out = [{'ts': ts5[_r[1] - 1], 'open': float(o5[_r[1] - 1]), 'high': float(h5[_r[1] - 1]),
            'low': float(l5[_r[1] - 1]), 'close': float(p5[_r[1] - 1]),
            'vol_first': float(v5[_r[1] - 1])}
           for _r in (day5.get(d) for d in days) if _r]
    today = day5.get(as_of)
    if today and today[1] > today[0]:
        i = today[0]
        out.append({'ts': ts5[i], 'open': float(o5[i]), 'high': float(h5[i]),
                    'low': float(l5[i]), 'close': float(p5[i]), 'vol_first': float(v5[i])})
    return out


def scan(tickers, as_of: str, dry_run: bool, schema: str):
    from datetime import datetime, timedelta
    from strategies.trend_sma.data_provider import load_ticker_bars
    from strategies.trend_sma.detect import detect
    from tqa_f.data.bars import utc_date

    start = (datetime.strptime(as_of, '%Y-%m-%d') - timedelta(days=WARMUP_DAYS + 30)).strftime('%Y-%m-%d')
    pg = None
    if not dry_run:
        from tqa_f.state.pg_state import PGState
        pg = PGState()
        pg.ensure_tables_live(schema)

    total = 0
    for ticker in tickers:
        bars = load_ticker_bars(ticker, start, as_of)
        if not bars:
            print(f"{ticker}: нет данных")
            continue
        sig_bars = _daily_window(bars, as_of)
        signals = detect(sig_bars, dict(DETECT_CONFIG, symbol=ticker))
        print(f"{ticker}: {len(bars)} M1, {len(sig_bars)} дневных → {len(signals)} сигналов")
        for s in signals:
            row = {'strategy': 'trend_sma', 'symbol': ticker, 'direction': s.direction,
                   'price': float(s.price), 'timestamp': f'{as_of} 12:00:00+03',
                   'reason': getattr(s, 'reason', ''), 'as_of': as_of}
            total += 1
            if pg is not None:
                pg.save_signal(schema, row)
    print(f"Итого сигналов: {total}" + ("" if pg is not None else " (dry-run, PG не тронут)"))
    return total


def main():
    ap = argparse.ArgumentParser(description='Detector CLI')
    sub = ap.add_subparsers(dest='command')

    sub.add_parser('check', help='Check self-sufficiency')

    sp = sub.add_parser('scan', help='Scan bars and detect signals')
    sp.add_argument('--tickers', required=True)
    sp.add_argument('--as-of', default=None, help='дата решения YYYY-MM-DD (default: последняя в CH)')
    sp.add_argument('--from', dest='date_from', default=None, help='диапазон дат решений (с)')
    sp.add_argument('--to', dest='date_to', default=None, help='диапазон дат решений (по)')
    sp.add_argument('--schema', default='futures')
    sp.add_argument('--dry-run', action='store_true')

    args = ap.parse_args()
    if args.command == 'check':
        check_self_sufficiency()
    elif args.command == 'scan':
        tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]
        as_of = args.as_of
        if not as_of:
            from strategies.trend_sma.data_provider import load_ticker_bars
            from tqa_f.data.bars import utc_date
            bars = load_ticker_bars(tickers[0], '2000-01-01')
            as_of = str(max(utc_date(b['ts']) for b in bars))
        if args.date_from and args.date_to:
            from datetime import datetime, timedelta
            d = datetime.strptime(args.date_from, '%Y-%m-%d')
            d1 = datetime.strptime(args.date_to, '%Y-%m-%d')
            total = 0
            while d <= d1:
                total += scan(tickers, d.strftime('%Y-%m-%d'), args.dry_run, args.schema)
                d += timedelta(days=1)
            print(f"Всего сигналов за диапазон: {total}")
        else:
            scan(tickers, as_of, args.dry_run, args.schema)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
