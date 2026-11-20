#!/usr/bin/env python3
"""IR PnL Tracker v3 — multi-TF, compound, M1 MTM, реалистичные издержки.

Ресемплирует 5M бары в H1, H4, D1.
Подаёт на detect и считает PnL с полным спредом (AlfaForex).
"""

import sys, json, argparse, time, statistics
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, '/home/user/projects/tqa-f')
from strategies.trend_sma.data_provider import load_ticker_bars as ltb
from strategies.ir.detect import detect
from tqa_f.data import forex_specs


def resample(bars, tf_min):
    """Ресемпл 5M→tf_min (H1=60, H4=240, D1=1440). Возвращает list[dict]."""
    buckets = defaultdict(list)
    for b in bars:
        t = b['ts']
        if tf_min >= 1440:
            key = t.date()
        else:
            minute_block = (t.hour * 60 + t.minute) // tf_min * tf_min
            key = t.replace(hour=minute_block // 60, minute=minute_block % 60, second=0, microsecond=0)
        buckets[key].append(b)
    out = []
    for key in sorted(buckets):
        bb = buckets[key]
        out.append({
            'ts': key if isinstance(key, datetime) else datetime.combine(key, datetime.min.time()),
            'opn': bb[0]['opn'],
            'hi': max(b['hi'] for b in bb),
            'lo': min(b['lo'] for b in bb),
            'prc': bb[-1]['prc'],
            'vol': sum(b.get('vol', 0) for b in bb),
        })
    return out


def run_ticker(ticker, start, end, capital, risk=0.65, tf_min=60):
    """Прогон одного тикера на заданном ТФ."""
    bars5 = ltb(ticker, start, end)
    if not bars5:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signals': 0, 'final_eq': capital}

    # Ресемпл
    bars = resample(bars5, tf_min)
    if not bars:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signals': 0, 'final_eq': capital}

    # Detect — i/w/h константы (48 баров на любом ТФ)
    i_val, w_val, h_val = 48, 48, 12
    cfg = {'i': i_val, 'w': w_val, 'h': h_val, 'symbol': ticker}
    sigs = detect(bars, cfg)
    if not sigs:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signals': 0, 'final_eq': capital}

    # Спека для издержек
    sp = forex_specs.SPECS.get(ticker, {})
    quote_ccy = sp.get('quote', 'USD')
    pt = sp.get('point', 0)
    half_pts = sp.get('spread', 0) / 2.0
    half_val = half_pts * pt
    contract = sp.get('contract', 100_000)

    rate_sym, rate_invert = forex_specs.QUOTE_RATE.get(quote_ccy, (None, False)) if quote_ccy != 'USD' else (None, False)
    rate_bars = ltb(rate_sym, start, end) if rate_sym and rate_sym != ticker else None
    rate_price = rate_bars[-1]['prc'] if rate_bars else 1.0

    ts2idx = {b['ts']: i for i, b in enumerate(bars)}

    cum_pnl = 0.0
    max_m1_mdd = 0.0
    trade_count = 0
    equity_curve = [capital]

    for s in sigs:
        r = json.loads(s.reason)
        if r.get('tr', 0) != 0:
            continue
        direc = 1 if s.direction == 'LONG' else -1
        if direc not in (1, -1):
            continue
        entry_price_raw = s.price
        cur_eq = capital + cum_pnl
        invest = risk * cur_eq
        if invest <= 0:
            continue

        # Спред на вход
        entry_price = entry_price_raw + half_val if direc == 1 else entry_price_raw - half_val
        units_base = invest / entry_price
        spread_cost_quote = half_pts * pt * units_base
        if rate_bars and quote_ccy != 'USD':
            spread_cost_usd = spread_cost_quote / rate_price if rate_invert else spread_cost_quote * rate_price
        else:
            spread_cost_usd = spread_cost_quote
        cur_eq_after_entry = cur_eq - spread_cost_usd

        try:
            sig_ts = s.timestamp if isinstance(s.timestamp, datetime) else datetime.strptime(s.timestamp, '%Y-%m-%d %H:%M:%S')
            bi = ts2idx.get(sig_ts)
            if bi is None:
                bi = ts2idx.get(bars[0]['ts'])  # fallback
        except (ValueError, TypeError):
            continue
        exit_bi = min(bi + 1, len(bars) - 1)
        exit_price_raw = bars[exit_bi]['prc']

        # Спред на выход
        exit_price = exit_price_raw - half_val if direc == 1 else exit_price_raw + half_val

        # PnL
        if direc == 1:
            pnl_quote = units_base * (exit_price - entry_price)
        else:
            pnl_quote = units_base * (entry_price - exit_price)

        if rate_bars and quote_ccy != 'USD':
            pnl = pnl_quote / rate_price if rate_invert else pnl_quote * rate_price
        else:
            pnl = pnl_quote

        # M1 MTM на каждом баре внутри сделки
        trade_peak_eq = cur_eq_after_entry
        for j in range(bi, exit_bi + 1):
            b = bars[j]
            if direc == 1:
                mtm_hi = cur_eq_after_entry + units_base * (b['hi'] - entry_price)
                mtm_lo = cur_eq_after_entry + units_base * (b['lo'] - entry_price)
            else:
                mtm_hi = cur_eq_after_entry + units_base * (entry_price - b['lo'])
                mtm_lo = cur_eq_after_entry + units_base * (entry_price - b['hi'])
            best = max(mtm_hi, mtm_lo)
            worst = min(mtm_hi, mtm_lo)
            if best > trade_peak_eq:
                trade_peak_eq = best
            if trade_peak_eq > 0:
                dd = (trade_peak_eq - worst) / trade_peak_eq * 100
                if dd > max_m1_mdd:
                    max_m1_mdd = dd

        cum_pnl += pnl
        equity_curve.append(capital + cum_pnl)
        trade_count += 1

    roi_pct = cum_pnl / capital * 100
    return {'roi': roi_pct, 'mdd': max_m1_mdd, 'trades': trade_count, 'signals': len(sigs),
            'final_eq': capital + cum_pnl, 'bars': len(bars), 'i': i_val, 'w': w_val, 'h': h_val,
            'tf_label': f'{tf_min}min'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-01-01')
    ap.add_argument('--end', default='2026-11-19')
    ap.add_argument('--tickers', default='eurusd,gbpusd,usdjpy,audjpy')
    ap.add_argument('--capital', type=float, default=3500.0)
    ap.add_argument('--risk', type=float, default=0.65)
    ap.add_argument('--tf', type=int, default=60, help='Таймфрейм минут: 60=H1, 240=H4, 1440=D1')
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',')]
    cap_per = args.capital / len(tickers)

    print('=' * 100)
    print(f'IR PnL v3 — TF={args.tf}min, compound, M1 MTM, издержки')
    print(f'Capital: {args.capital:.0f} USD ({len(tickers)} x {cap_per:.0f})')
    print(f'Period: {args.start} -> {args.end}, Risk: {args.risk*100:.0f}%')
    print(f'Спреды: AlfaForex, конверсия quote→USD')
    print('=' * 100)

    results = {}
    for t in tickers:
        t0 = time.time()
        r = run_ticker(t, args.start, args.end, cap_per, risk=args.risk, tf_min=args.tf)
        dt = time.time() - t0
        results[t] = r
        ratio = r['roi'] / r['mdd'] if r['mdd'] > 0 else 0
        print(f'\n--- {t.upper()} (i={r["i"]}, w={r["w"]}, h={r["h"]}, {r["bars"]} bars, {dt:.1f}s) ---')
        print(f'  Signals: {r["signals"]}  Trades: {r["trades"]}')
        print(f'  ROI: {r["roi"]:+.2f}%  M1 MDD: {r["mdd"]:+.2f}%  Ratio: {ratio:.2f}')
        print(f'  Final eq: {r["final_eq"]:.2f}')

    print('\n' + '=' * 100)
    print(f'FINAL SUMMARY (TF={args.tf}min)')
    print('=' * 100)
    print(f'{"Ticker":>8} {"ROI%":>10} {"M1MDD%":>8} {"Ratio":>8} {"Trades":>7} {"MD<15%":>7}')
    print('-' * 60)

    total_roi = 0.0; total_mdd = 0.0; total_trades = 0; total_signals = 0; pass_c = 0
    for t in tickers:
        r = results[t]
        md15 = 'Y' if r['mdd'] < 15 and r['roi'] > 0 else 'N'
        ratio = r['roi'] / r['mdd'] if r['mdd'] > 0 else 0
        if md15 == 'Y': pass_c += 1
        print(f'{t:>8} {r["roi"]:>+9.2f}% {r["mdd"]:>+8.2f}% {ratio:>8.2f} {r["trades"]:>7d} {md15:>7s}')
        total_roi += r['roi']; total_mdd += r['mdd']
        total_trades += r['trades']; total_signals += r['signals']

    avg_mdd = total_mdd / len(tickers) if tickers else 0
    print(f'\nSUM ROI: {total_roi:+.2f}%  AVG M1 MDD: {avg_mdd:+.2f}%')
    print(f'Trades: {total_trades}  Signals: {total_signals}  Pass MDD<15%: {pass_c}/{len(tickers)}')

    total_final_eq = sum(results[t]['final_eq'] for t in tickers)
    print(f'Total final equity: {total_final_eq:.2f}')


if __name__ == '__main__':
    main()