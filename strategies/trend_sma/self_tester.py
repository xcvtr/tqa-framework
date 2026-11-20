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

sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.engine import (  # noqa: E402,F401  (реэкспорт хелперов)
    Config, MarketData, State, _group_days, force_close, utc_date, mtm_mdd, resample5, run_days,
)

log = logging.getLogger("tqa_f.runner")


def load_specs(tickers: list[str]) -> dict:
    """Спеки форекс-инструментов (ms=пункт, sp=стоимость пункта на лот)."""
    from tqa_f.data.forex_specs import spec
    out = {}
    for t in tickers:
        try:
            s = spec(t)
        except KeyError:
            log.warning('%s: нет спека, беру eurusd-подобный', t)
            s = spec('eurusd')
        out[t] = {'go': 0.0, 'ms': s['ms'], 'sp': s['sp'], 'fee': s['commission'],
                  'spread': s.get('spread', 0.0), 'swap_l': s.get('swap_l', 0.0),
                  'swap_s': s.get('swap_s', 0.0), 'margin': s.get('margin', 0.0)}
    return out


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
    parser.add_argument('--go-knur', type=float, default=1.0,
                        help='потолок маржи как доля equity (forex=1.0; 8.0 — фьючерсный канон MOEX)')
    parser.add_argument('--eq-cap', type=float, default=200_000, help='кап equity для сайзинга (0 = без капа, компаунд как в каноне 106)')
    parser.add_argument('--eq0', type=float, default=None, help='стартовый equity (по умолчанию = eq-cap или 200000)')
    parser.add_argument('--no-pyramid', action='store_true', help='отключить пирамидинг (усилитель)')
    parser.add_argument('--pyramid-max', type=int, default=0, help='ступени пирамидинга (0 = выкл)')
    parser.add_argument('--days', type=int, default=365, help='торговых дней истории (как canon --days)')
    parser.add_argument('--out', type=str, default='', help='путь к JSON для выгрузки сделок')
    parser.add_argument('--persist', choices=['none', 'pg'], default='none',
                        help='pg = писать state/trades в PG (по умолчанию none)')
    parser.add_argument('--pg-schema', default='tqa_f_bt', help='схема PG для state-journal')
    parser.add_argument('--reset-state', action='store_true', help='очистить state перед прогоном')
    parser.add_argument('--connector', choices=['none', 'mock'], default='none')
    parser.add_argument('--signals', choices=['local', 'pg'], default='local',
                        help='local = detect в процессе; pg = читать pending_signals')
    parser.add_argument('--signals-schema', default='tqa_f_sig')
    parser.add_argument('--sma', default='50/100', help='sma_fast/sma_slow')
    parser.add_argument('--sma-by-ticker', default='', help='audjpy=50/100,eurusd=20/60')
    parser.add_argument('--ma-type', choices=['sma', 'ema'], default='sma')
    parser.add_argument('--fixed-lots', type=float, default=1.0, help='0 = сайзинг по equity')
    parser.add_argument('--lot-step', type=float, default=0.0, help='>0 = дробный сайзинг FX с шагом (0.01)')
    parser.add_argument('--regime-er', type=float, default=0.0,
                        help='>0 = запрет новых входов, если ER за 50 дней ниже порога (защита от боковика)')
    parser.add_argument('--regime-metric', default='er',
                        choices=['er', 'chop', 'vr5', 'vr10', 'r2', 'sig', 'ac1', 'trendz', 'body', 'fb'],
                        help='метрика режима для гейта')
    parser.add_argument('--regime-op', default='>=', choices=['>=', '<='], help='направление гейта')
    parser.add_argument('--regime-thr', type=float, default=None, help='порог метрики режима')
    parser.add_argument('--dd-stop', type=float, default=None,
                        help='доля просадки от пика счёта, при которой запрещаются новые входы')
    parser.add_argument('--dd-cool', type=int, default=None,
                        help='торговых дней паузы, после которых торговля возвращается принудительно')
    parser.add_argument('--dd-resume', type=float, default=None,
                        help='просадка, при которой торговля возобновляется (по умолчанию половина --dd-stop)')
    parser.add_argument('--sl-pct', type=float, default=1.5)
    parser.add_argument('--trail-pct', type=float, default=1.5)
    parser.add_argument('--arm-pct', type=float, default=2.0)
    parser.add_argument('--lock-pct', type=float, default=0.0,
                        help='Ratchet stop: стоп не выше entry±lock_pct от пика; 0=выкл')
    parser.add_argument('--no-pyramid-flag', action='store_true', default=True)
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',')]
    _f, _s = (int(x) for x in args.sma.split('/'))
    params_by_ticker = {t: {'sma_fast': _f, 'sma_slow': _s, 'ma_type': args.ma_type} for t in tickers}
    for item in filter(None, (x.strip() for x in args.sma_by_ticker.split(','))):
        tk, pair = item.split('=')
        _parts = pair.split('/')
        f2, s2 = int(_parts[0]), int(_parts[1])
        _ma = _parts[2] if len(_parts) > 2 else args.ma_type
        params_by_ticker[tk.strip()] = {'sma_fast': f2, 'sma_slow': s2, 'ma_type': _ma}

    # Канон: грузим days + window_days (150) КАЛЕНДАРНЫХ дней до --end как предысторию.
    _end_dt = _dt.strptime(args.end, '%Y-%m-%d') if args.end else _dt.now()
    _load_start = (_end_dt - _td2(days=int(args.days) + 150)).strftime('%Y-%m-%d')
    log.info(f"Warmup: load from {_load_start}, trade starts {args.start}")

    md = build_market(tickers, _load_start, args.end)
    if not md.dates:
        print("[ERROR] No data loaded"); return 1
    log.info(f"Timeline: {len(md.dates)} trading days ({md.dates[0]} → {md.dates[-1]})")

    eq0 = float(args.eq0) if args.eq0 is not None else (float(args.eq_cap) if args.eq_cap > 0 else 200_000.0)
    connector = None
    if args.connector == 'mock':
        from tqa_f.connector.exchange_mock import ExchangeMock
        connector = ExchangeMock()
    signal_source = None
    if args.signals == 'pg':
        from strategies.trend_sma.signals_pg import PGSignalSource
        signal_source = PGSignalSource(args.signals_schema)
    from strategies.trend_sma.broker import BrokerSim
    from tqa_f.data.forex_specs import rate_symbols
    from strategies.trend_sma.data_provider import load_ticker_bars
    import numpy as _np
    _RATE_KEY = {'usdjpy': 'JPY', 'usdcad': 'CAD', 'usdchf': 'CHF',
                 'audusd': 'AUD', 'gbpusd': 'GBP', 'eurusd': 'EUR'}
    rates = {}
    for _rs in rate_symbols():
        _rb = load_ticker_bars(_rs, _load_start, args.end)
        if _rb:
            rates[_RATE_KEY[_rs]] = (
                _np.array([int(b['ts'].timestamp()) for b in _rb], dtype='int64'),
                _np.array([b['prc'] for b in _rb], dtype='float64'))
    cfg = Config(start=args.start, risk_pct=args.risk_pct, go_knur=args.go_knur,
                 eq_cap=args.eq_cap, no_pyramid=args.no_pyramid,
                 specs=load_specs(md.tickers), connector=connector,
                 signal_source=signal_source, params_by_ticker=params_by_ticker,
                 fixed_lots=args.fixed_lots, rates=rates, lot_step=args.lot_step,
                 dd_stop=args.dd_stop, dd_resume=args.dd_resume, dd_cool=args.dd_cool,
                 regime_er=args.regime_er,
                 regime_metric=args.regime_metric, regime_op=args.regime_op,
                 regime_thr=(args.regime_thr if args.regime_thr is not None
                             else (args.regime_er if args.regime_er > 0 else None)),
                 trailing_params={'trail_mode': 'trend_sma', 'stop_loss': args.sl_pct,
                                  'activation': args.arm_pct, 'trail': args.trail_pct,
                                  'oi_pyramid_max': 0 if args.no_pyramid else args.pyramid_max},
                 broker=BrokerSim(commission=3.5, slippage_in=0.6, slippage_out=0.6))
    st = State(eq0)

    run_days(md, st, cfg, md.dates)
    force_close(md, st, cfg)

    max_dd, peak_equity, _mdd_low = mtm_mdd(md, cfg, st.closed_trades, eq0)
    closed_trades, equity = st.closed_trades, st.equity

    roi = (equity - eq0) / eq0 * 100
    wins = [t for t in closed_trades if t['pnl'] > 0]
    losses = [t for t in closed_trades if t['pnl'] <= 0]
    wr = len(wins) / len(closed_trades) * 100 if closed_trades else 0

    print("=" * 60)
    print("TQA-M BACKTEST RESULT")
    print("=" * 60)
    print(f"Start equity:     {eq0:>12,.0f} USD")
    print(f"Final equity:     {equity:>12,.0f} USD")
    print(f"Realized PnL:     {sum(t['pnl'] for t in closed_trades):>12,.2f} USD")
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
        from tqa_f.data.forex_specs import spec as _spec
        for tk in sorted(tk_stats):
            s = tk_stats[tk]
            wr_stk = s['wins'] / s['trades'] * 100 if s['trades'] else 0
            _pp = _spec(tk)['sp']   # стоимость пункта на лот (было contract*pip)
            _pips = sum(t['pnl'] / _pp for t in closed_trades if t['ticker'] == tk)
            print(f"  {tk:>7}: {s['trades']:3d} trades | PnL={s['pnl']:>10,.2f} USD "
                  f"| {_pips:>8.1f} пп | WR={wr_stk:.1f}%")

    if args.out:
        import json as _json
        with open(args.out, 'w') as f:
            _json.dump(closed_trades, f, ensure_ascii=False, indent=1, default=str)
        print(f"Saved {len(closed_trades)} trades → {args.out}")

    if args.persist == 'pg':
        from tqa_f.state.pg_state import PaperDB
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
