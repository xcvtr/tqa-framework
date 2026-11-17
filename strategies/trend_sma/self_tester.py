"""TQA-M self-tester: ДРАЙВЕР движка на истории CH.

Архитектура: detector читает ТОЛЬКО CH (immutable raw bars), executor исполняет
SL/trailing/pyramid, PG — только state journal. Логика решений живёт в engine.py,
и ровно та же функция вызывается live-драйвером на скользящем окне (см. live.py).

Паритет с каноном (backtest.summary id=106) — гейт scripts/parity.sh.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime as _dt, timedelta as _td2

sys.path.insert(0, '/home/user/projects/tqa-m')

from strategies.trend_sma.engine import (  # noqa: E402,F401  (реэкспорт хелперов)
    Config, MarketData, State, _group_days, force_close, msk_date, mtm_mdd, resample5, run_days,
)

log = logging.getLogger("tqa_m.runner")


def load_specs(tickers: list[str]) -> dict:
    """Спеки тикеров из PG (go, ms, sp, fee) — один запрос на все."""
    import psycopg2
    conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
    cur = conn.cursor()
    cur.execute("SELECT ticker, COALESCE(go_pgo, go_exch, go), min_step, step_price, fee_entry "
                "FROM futures.ticker_specs WHERE ticker = ANY(%s)", (tickers,))
    specs = {t: {'go': float(go or 0), 'ms': float(ms or 1.0), 'sp': float(sp or 1.0),
                 'fee': float(fee or 4.0)} for t, go, ms, sp, fee in cur.fetchall()}
    cur.close(); conn.close()
    return specs


def build_market(tickers: list[str], load_start: str, end: str | None) -> MarketData:
    from strategies.trend_sma.data_provider import load_ticker_bars
    md = MarketData()
    for ticker in tickers:
        bars = load_ticker_bars(ticker, load_start, end)
        if not bars:
            log.warning(f"{ticker}: no data")
            continue
        md.add_ticker(ticker, bars)
        log.info(f"{ticker}: {len(bars)} M1 bars, {len(md.b5_ts[ticker])} 5m bars")
        del bars
    md.finalize()
    return md


def main():
    parser = argparse.ArgumentParser(description="TQA-M Backtest (trend_sma)")
    parser.add_argument('--tickers', default='CM,AL,SV,NA,MN,VK,SG,HY')
    parser.add_argument('--start', required=True, help='YYYY-MM-DD')
    parser.add_argument('--end', default=None, help='YYYY-MM-DD')
    parser.add_argument('--risk-pct', type=float, default=0.025)
    parser.add_argument('--go-knur', type=float, default=8.0, help='ГО-кап как доля equity (canon S2=8.0)')
    parser.add_argument('--eq-cap', type=float, default=200_000, help='кап equity для сайзинга (0 = без капа, компаунд как в каноне 106)')
    parser.add_argument('--no-pyramid', action='store_true', help='отключить пирамидинг (усилитель)')
    parser.add_argument('--days', type=int, default=365, help='торговых дней истории (как canon --days)')
    parser.add_argument('--out', type=str, default='', help='путь к JSON для выгрузки сделок')
    parser.add_argument('--persist', choices=['none', 'pg'], default='none',
                        help='pg = писать state/trades в PG (по умолчанию none)')
    parser.add_argument('--pg-schema', default='tqa_m_bt', help='схема PG для state-journal')
    parser.add_argument('--reset-state', action='store_true', help='очистить state перед прогоном')
    parser.add_argument('--connector', choices=['none', 'mock'], default='none')
    parser.add_argument('--signals', choices=['local', 'pg'], default='local',
                        help='local = detect в процессе; pg = читать pending_signals')
    parser.add_argument('--signals-schema', default='futures')
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',')]

    # Канон: грузим days + window_days (150) КАЛЕНДАРНЫХ дней до --end как предысторию.
    _end_dt = _dt.strptime(args.end, '%Y-%m-%d') if args.end else _dt.now()
    _load_start = (_end_dt - _td2(days=int(args.days) + 150)).strftime('%Y-%m-%d')
    log.info(f"Warmup: load from {_load_start}, trade starts {args.start}")

    md = build_market(tickers, _load_start, args.end)
    if not md.dates:
        print("[ERROR] No data loaded"); return 1
    log.info(f"Timeline: {len(md.dates)} trading days ({md.dates[0]} → {md.dates[-1]})")

    eq0 = float(args.eq_cap) if args.eq_cap > 0 else 200_000.0
    connector = None
    if args.connector == 'mock':
        from tqa_m.connector.exchange_mock import ExchangeMock
        connector = ExchangeMock()
    signal_source = None
    if args.signals == 'pg':
        from strategies.trend_sma.signals_pg import PGSignalSource
        signal_source = PGSignalSource(args.signals_schema)
    cfg = Config(start=args.start, risk_pct=args.risk_pct, go_knur=args.go_knur,
                 eq_cap=args.eq_cap, no_pyramid=args.no_pyramid,
                 specs=load_specs(md.tickers), connector=connector,
                 signal_source=signal_source)
    st = State(eq0)

    run_days(md, st, cfg, md.dates)
    force_close(md, st, cfg)

    max_dd, peak_equity, _mdd_low = mtm_mdd(md, cfg, st.closed_trades)
    closed_trades, equity = st.closed_trades, st.equity

    roi = (equity - eq0) / eq0 * 100
    wins = [t for t in closed_trades if t['pnl'] > 0]
    losses = [t for t in closed_trades if t['pnl'] <= 0]
    wr = len(wins) / len(closed_trades) * 100 if closed_trades else 0

    print("=" * 60)
    print("TQA-M BACKTEST RESULT")
    print("=" * 60)
    print(f"Start equity:     {args.eq_cap:>12,.0f} RUB")
    print(f"Final equity:     {equity:>12,.0f} RUB")
    print(f"Realized PnL:     {sum(t['pnl'] for t in closed_trades):>12,.2f} RUB")
    print(f"ROI:              {roi:>11.2f}%")
    print(f"Peak equity:      {peak_equity:>12,.0f} RUB")
    print(f"Max DD:           {max_dd*100:>11.2f}%")
    print(f"Total trades:     {len(closed_trades):>12d}")
    print(f"Win rate:         {wr:>11.2f}% ({len(wins)}W/{len(losses)}L)")
    print(f"Signals generated:{st.total_signals:>12d}")
    print("=" * 60)

    if closed_trades:
        print("\nPer-ticker:")
        tk_stats = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
        for t in closed_trades:
            s = tk_stats[t['ticker']]
            s['trades'] += 1; s['pnl'] += t['pnl']
            if t['pnl'] > 0:
                s['wins'] += 1
        for tk in sorted(tk_stats):
            s = tk_stats[tk]
            wr_stk = s['wins'] / s['trades'] * 100 if s['trades'] else 0
            print(f"  {tk:>6}: {s['trades']:3d} trades | PnL={s['pnl']:>10,.2f} RUB | WR={wr_stk:.1f}%")

    if args.out:
        import json as _json
        with open(args.out, 'w') as f:
            _json.dump(closed_trades, f, ensure_ascii=False, indent=1, default=str)
        print(f"Saved {len(closed_trades)} trades → {args.out}")

    if args.persist == 'pg':
        from tqa_m.state.pg_state import PaperDB
        from datetime import timezone as _tz
        import psycopg2 as _pg
        _conn = _pg.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
        _db = PaperDB(_conn, args.pg_schema)
        _db.ensure(eq0)
        if args.reset_state:
            _db.reset()
            _db.ensure(eq0)
        for _t in closed_trades:
            _db.save_trade({
                'strategy': 'trend_sma',
                'symbol': _t['ticker'],
                'direction': 'LONG' if _t['direction'] == 'long' else 'SHORT',
                'pnl_rub': float(_t['pnl']),
                'ts_open': _dt.fromtimestamp(int(_t['entry_i']), _tz.utc),
                'ts_close': _dt.fromtimestamp(int(_t['exit_i']), _tz.utc),
                'entry_price': float(_t['entry_price']), 'exit_price': float(_t['exit_price']),
                'reason': _t['reason'], 'shares': int(_t['shares']),
            })
        _db.save({'equity': float(equity), 'peak': float(peak_equity), 'balance': float(eq0),
                  'positions': {}})
        print(f"Persisted to PG {args.pg_schema}: {len(closed_trades)} trades, equity {equity:,.0f}")
        _conn.close()

    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sys.exit(main() or 0)
