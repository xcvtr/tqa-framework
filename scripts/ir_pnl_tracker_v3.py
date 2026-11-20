#!/usr/bin/env python3
"""IR PnL Tracker v3 — compound + next-bar exit, M1 MTM MDD + реалистичные издержки.

Издержки по forex_specs: спред (½ на вход, ½ на выход), конверсия quote→USD.
Без свопов (все сделки 1 бар, не держим овернайт). Без комиссий (AlfaForex 0).
"""

import sys, json, argparse
from datetime import datetime

sys.path.insert(0, '/home/user/projects/tqa-f')
from strategies.trend_sma.data_provider import load_ticker_bars as ltb
from strategies.ir.detect import detect
from tqa_f.data import forex_specs


def run_ticker(ticker, start, end, capital, risk=0.02):
    bars = ltb(ticker, start, end)
    if not bars:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signals': 0, 'final_eq': capital}
    cfg = {'i': 48, 'w': 48, 'h': 12, 'symbol': ticker}
    sigs = detect(bars, cfg)
    if not sigs:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signals': 0, 'final_eq': capital}

    sp = forex_specs.SPECS.get(ticker, {})
    quote_ccy = sp.get('quote', 'USD')
    pt = sp.get('point', 0)
    half_pts = sp.get('spread', 0) / 2.0
    half_val = half_pts * pt
    contract = sp.get('contract', 100_000)

    # Rate-бары для конверсии PnL if quote ≠ USD
    rate_sym, rate_invert = forex_specs.QUOTE_RATE.get(quote_ccy, (None, False)) if quote_ccy != 'USD' else (None, False)
    rate_bars = ltb(rate_sym, start, end) if rate_sym and rate_sym != ticker else None

    ts2idx = {b['ts']: i for i, b in enumerate(bars)}

    cum_pnl = 0.0
    max_m1_mdd = 0.0
    trade_count = 0

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
        if direc == 1:
            entry_price = entry_price_raw + half_val
        else:
            entry_price = entry_price_raw - half_val

        # Размер позиции в единицах базовой валюты
        units_base = invest / entry_price  # это не контракты, а доля базовой валюты
        # Для спреда: стоимость пол-спреда в quote-валюте
        # point_value_per_lot = contract * pt
        # lots = units_base / contract
        # spread_cost_quote = half_pts * point_value_per_lot * lots = half_pts * pt * units_base
        spread_cost_quote = half_pts * pt * units_base

        # Конвертируем spread_cost в USD
        if rate_bars and quote_ccy != 'USD':
            rate_price = rate_bars[-1]['prc']
            spread_cost_usd = spread_cost_quote / rate_price if rate_invert else spread_cost_quote * rate_price
        else:
            spread_cost_usd = spread_cost_quote

        cur_eq_after_entry = cur_eq - spread_cost_usd

        try:
            sig_ts = datetime.strptime(s.timestamp, '%Y-%m-%d %H:%M:%S')
            bi = ts2idx[sig_ts]
        except (KeyError, ValueError):
            continue
        exit_bi = min(bi + 1, len(bars) - 1)
        exit_price_raw = bars[exit_bi]['prc']

        # Спред на выход
        if direc == 1:
            exit_price = exit_price_raw - half_val
        else:
            exit_price = exit_price_raw + half_val

        # PnL сделки
        if direc == 1:
            pnl_quote = units_base * (exit_price - entry_price)
        else:
            pnl_quote = units_base * (entry_price - exit_price)

        # Конвертация PnL в USD
        if rate_bars and quote_ccy != 'USD':
            if rate_price > 0:
                pnl = pnl_quote / rate_price if rate_invert else pnl_quote * rate_price
            else:
                pnl = pnl_quote
        else:
            pnl = pnl_quote

        # M1 MTM: на каждом баре внутри сделки
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
        trade_count += 1

    roi_pct = cum_pnl / capital * 100
    return {'roi': roi_pct, 'mdd': max_m1_mdd, 'trades': trade_count, 'signals': len(sigs), 'final_eq': capital + cum_pnl}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-01-01')
    ap.add_argument('--end', default='2026-11-19')
    ap.add_argument('--tickers', default='eurusd,gbpusd,usdjpy,audjpy')
    ap.add_argument('--capital', type=float, default=3500.0)
    ap.add_argument('--risk', type=float, default=0.02)
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',')]
    cap_per = args.capital / len(tickers)

    print('=' * 95)
    print('IR PnL TRACKER v3 — compound + next-bar, M1 MTM, издержки по forex_specs')
    print(f'Capital: {args.capital:.0f} USD ({len(tickers)} x {cap_per:.0f})')
    print(f'Period: {args.start} -> {args.end}')
    print(f'Risk: {args.risk*100:.0f}%')
    print(f'Спреды AlfaForex (½ на вход+выход), конверсия quote→USD')
    print('=' * 95)

    results = {}
    for t in tickers:
        print(f'\n--- {t.upper()} ---')
        r = run_ticker(t, args.start, args.end, cap_per, risk=args.risk)
        results[t] = r
        ratio = r['roi'] / r['mdd'] if r['mdd'] > 0 else 0
        print(f'  Signals: {r["signals"]}  Trades: {r["trades"]}')
        print(f'  ROI: {r["roi"]:+.2f}%  M1 MDD: {r["mdd"]:+.2f}%  Ratio: {ratio:.2f}')
        print(f'  Final eq: {r["final_eq"]:.2f}')

    print('\n' + '=' * 95)
    print('FINAL SUMMARY')
    print('=' * 95)
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