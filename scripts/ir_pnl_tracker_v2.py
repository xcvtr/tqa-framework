"""IR Strategy PnL Tracker v2 — improved exit logic using signal timestamps.

Key improvements:
- Entry at signal timestamp
- Exit at NEXT bar open (5m bar after M1 signal) or at day end
- Proper running equity curve for MDD calculation
- Still: 500 USD start, no commissions/swaps
"""

import sys
sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.data_provider import load_ticker_bars as ltb
from strategies.ir.detect import detect
from tqa_f.connector.exchange_base import Signal
import json
from datetime import datetime, timedelta


def compute_pnl_v2(ticker, start, end, capital=500.0, exit_bar_offset=1):
    """Compute ROI/MDD using signal timestamps for entry/exit.
    
    exit_bar_offset: how many bars after signal to exit (1 = next bar)
    """
    
    bars = ltb(ticker, start, end)
    config = {'i': 24, 'w': 24, 'h': 12, 'symbol': ticker}
    signals = detect(bars, config)
    
    n_sigs = len(signals)
    if n_sigs == 0:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signal_count': 0}
    
    # Build bar lookup: ts -> bar index
    bar_times = [b['ts'] for b in bars]
    
    entries = []
    exits = []
    directions = []
    signal_ts_list = []
    
    for s in signals:
        reason = json.loads(s.reason)
        tr = reason.get('tr', 0)
        
        if tr == 0:
            continue
        
        entries.append(s.price)
        directions.append(tr)
        signal_ts_list.append(datetime.strptime(s.timestamp, '%Y-%m-%d %H:%M:%S'))
    
    n_trades = len(entries)
    if n_trades == 0:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signal_count': n_sigs}
    
    # For each signal, find exit price at (signal_time + offset bars)
    # Signals are on M1, but we need to find the bar offset ahead
    
    pnls = []
    valid_trades = 0
    
    for idx, (entry, direc, sig_ts) in enumerate(zip(entries, directions, signal_ts_list)):
        # Find bar index for signal time
        # signal_ts is M1 timestamp, bars have M1 timestamps too
        try:
            sig_idx = bar_times.index(sig_ts)
        except ValueError:
            continue  # signal time not found in bars
        
        # Exit at sig_idx + exit_bar_offset
        exit_idx = sig_idx + exit_bar_offset
        if exit_idx >= len(bars):
            # Exit at last bar
            exit_idx = len(bars) - 1
        
        exit_bar = bars[exit_idx]
        exit_price = exit_bar['prc']
        exit_ts = exit_bar['ts']
        
        # Compute PnL
        if direc == 1:  # long
            pnl = (exit_price - entry) / entry * capital
        else:  # short
            pnl = (entry - exit_price) / entry * capital
        
        pnls.append(pnl)
        valid_trades += 1
    
    if valid_trades == 0:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signal_count': n_sigs}
    
    # Compute ROI
    total_pnl = sum(pnls)
    roi = total_pnl / capital * 100  # percent
    
    # Compute MDD via running equity curve
    # Sort trades by signal timestamp
    trade_data = list(zip(signal_ts_list[:valid_trades], pnls[:valid_trades], directions[:valid_trades]))
    trade_data.sort(key=lambda x: x[0])  # sort by time
    
    equity = capital
    peak = capital
    max_dd = 0.0
    
    for ts, pnl, direc in trade_data:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    mdd = max_dd
    
    # ROI/MDD ratio
    roi_mdd_ratio = roi / mdd if mdd > 0 else None
    
    return {
        'roi': roi,
        'mdd': mdd,
        'trades': valid_trades,
        'signal_count': n_sigs,
        'roi_mdd_ratio': roi_mdd_ratio,
        'total_pnl': total_pnl
    }


def main():
    tickers = ['eurusd', 'gbpusd', 'usdjpy', 'audjpy']
    start = '2026-01-01'
    end = '2026-11-19'
    capital = 500.0
    
    print('=' * 80)
    print('IR STRATEGY: PnL TRACKER v2 (improved exit logic)')
    print(f'Capital: {capital} USD | Period: {start} → {end}')
    print('Exit: next bar after signal (1 bar offset)')
    print('No commissions/swaps')
    print('=' * 80)
    print()
    
    all_results = {}
    
    for ticker in tickers:
        print(f'Processing {ticker.upper()}...')
        result = compute_pnl_v2(ticker, start, end, capital, exit_bar_offset=1)
        all_results[ticker] = result
        
        print(f'  Signals: {result["signal_count"]}')
        print(f'  Trades: {result["trades"]}')
        print(f'  ROI: {result["roi"]:+.2f}%')
        print(f'  MDD: {result["mdd"]:+.2f}%')
        if result['roi_mdd_ratio'] is not None:
            print(f'  ROI/MDD ratio: {result["roi_mdd_ratio"]:.2f}')
        else:
            print(f'  ROI/MDD ratio: N/A (MDD=0)')
        print()
    
    # Final summary
    print('=' * 80)
    print('FINAL SUMMARY')
    print('=' * 80)
    print(f"{'Ticker':>8} {'ROI':>10} {'MDD':>10} {'Ratio':>10} {'Trades':>8} {'Signals':>8}")
    print('-' * 55)
    
    total_roi = 0.0
    total_mdd = 0.0
    total_trades = 0
    total_signals = 0
    count_with_ratio = 0
    
    for ticker in tickers:
        r = all_results[ticker]
        roi_str = '{:+.2f}%'.format(r['roi'])
        mdd_str = '{:+.2f}%'.format(r['mdd'])
        ratio_str = '{:.2f}'.format(r['roi_mdd_ratio']) if r['roi_mdd_ratio'] is not None else 'N/A'
        trades_str = str(r['trades'])
        sigs_str = str(r['signal_count'])
        
        line = ticker.rjust(8) + ' ' + roi_str.rjust(10) + ' ' + mdd_str.rjust(10) + ' ' + ratio_str.rjust(10) + ' ' + trades_str.rjust(8) + ' ' + sigs_str.rjust(8)
        print(line)
        
        total_roi += r['roi']
        total_mdd += r['mdd']
        total_trades += r['trades']
        total_signals += r['signal_count']
        if r['roi_mdd_ratio'] is not None:
            count_with_ratio += 1
    
    print()
    print(f'Total: ROI={total_roi:+.2f}%, MDD={total_mdd:+.2f}%, '
          f'Trades={total_trades}, Signals={total_signals}')
    print(f'Configs with valid ROI/MDD ratio: {count_with_ratio}/4')
    
    # Quality check per user criterion
    print()
    print('QUALITY CHECK: ROI/MDD (M1), MDD < 15% criterion:')
    for ticker in tickers:
        r = all_results[ticker]
        mdd_abs = abs(r['mdd'])
        if mdd_abs < 15 and r['roi_mdd_ratio'] is not None:
            print(f'  {ticker.upper()}: ✓ MDD={mdd_abs:.2f}% < 15%, ROI/MDD={r["roi_mdd_ratio"]:.2f}')
        elif mdd_abs >= 15:
            print(f'  {ticker.upper()}: ✗ MDD={mdd_abs:.2f}% >= 15% (fails criterion)')
        else:
            print(f'  {ticker.upper()}: ? MDD=0, ratio=N/A')


if __name__ == '__main__':
    main()
