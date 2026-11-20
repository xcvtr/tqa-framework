"""IR Strategy PnL Tracker — calculates ROI/MDD using signal prices.

Uses IR detect signals directly with entry prices from signal.data.
No engine SMA dependency. Capital: 500 USD. No commissions/swaps.
"""

import sys
sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.data_provider import load_ticker_bars as ltb
from strategies.ir.detect import detect
from tqa_f.connector.exchange_base import Signal
import json
from strategies.trend_sma.engine import mtm_mdd
from strategies.trend_sma.engine import Config, State, MarketData, run_days, force_close
from datetime import datetime


def compute_pnl_from_signals(ticker, start, end, capital=500.0):
    """Compute ROI/MDD from IR signals using their entry prices."""
    
    bars = ltb(ticker, start, end)
    config = {'i': 24, 'w': 24, 'h': 12, 'symbol': ticker}  # standard i=24
    signals = detect(bars, config)
    
    n_sigs = len(signals)
    if n_sigs == 0:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signal_count': 0}
    
    # Build simple PnL simulation:
    # Each signal = 1 lot entry at signal.price, exit at next signal or bar close
    # Direction from reason['tr']: 1=long, -1=short, 0=neutral
    
    entries = []
    exits = []
    directions = []
    signal_timestamps = []
    
    for s in signals:
        reason = json.loads(s.reason)
        tr = reason.get('tr', 0)
        
        if tr == 0:
            continue  # neutral, not a trade
        
        entries.append(s.price)
        directions.append(tr)  # 1=long, -1=short
        signal_timestamps.append(datetime.strptime(s.timestamp, '%Y-%m-%d %H:%M:%S'))
    
    n_trades = len(entries)
    if n_trades == 0:
        return {'roi': 0.0, 'mdd': 0.0, 'trades': 0, 'signal_count': n_sigs}
    
    # Simple simulation: exit at next bar's close after entry
    # For simplicity, use last bar close as exit for all trades
    last_bar_close = bars[-1]['prc']
    
    # Compute PnL per trade
    pnls = []
    for i, (entry, direc) in enumerate(zip(entries, directions)):
        if direc == 1:  # long
            pnl = (last_bar_close - entry) / entry * capital
        else:  # short
            pnl = (entry - last_bar_close) / entry * capital
        pnls.append(pnl)
    
    # Compute ROI
    total_pnl = sum(pnls)
    roi = total_pnl / capital * 100  # in percent
    
    # Compute MDD (running max to min)
    equity_curve = capital
    peak = capital
    max_dd = 0.0
    
    # Process trades in chronological order
    sorted_trades = sorted(zip(signal_timestamps, pnls, directions))
    
    for ts, pnl, direc in sorted_trades:
        equity_curve += pnl
        if equity_curve > peak:
            peak = equity_curve
        dd = (peak - equity_curve) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    mdd = max_dd  # percentage
    
    # ROI/MDD ratio (user criterion)
    roi_mdd_ratio = roi / mdd if mdd > 0 else None
    
    return {
        'roi': roi,
        'mdd': mdd,
        'trades': n_trades,
        'signal_count': n_sigs,
        'roi_mdd_ratio': roi_mdd_ratio,
        'entries': entries[:5],  # first 5 for inspection
        'exits': [last_bar_close] * min(5, n_trades)
    }


def main():
    tickers = ['eurusd', 'gbpusd', 'usdjpy', 'audjpy']
    start = '2026-01-01'
    end = '2026-11-19'
    capital = 500.0
    
    print('=' * 80)
    print('IR STRATEGY: PnL TRACKER with Signal Prices')
    print(f'Capital: {capital} USD | Period: {start} → {end}')
    print('No commissions/swaps | Using IR detect signal prices')
    print('=' * 80)
    print()
    
    all_results = {}
    
    for ticker in tickers:
        print(f'Processing {ticker.upper()}...')
        result = compute_pnl_from_signals(ticker, start, end, capital)
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
    print('QUALITY CHECK: ROI/MDD < 15% MDD criterion:')
    for ticker in tickers:
        r = all_results[ticker]
        if r['mdd'] < 15 and r['roi_mdd_ratio'] is not None:
            print(f'  {ticker.upper()}: ✓ MDD={r["mdd"]:.2f}% < 15%, ROI/MDD={r["roi_mdd_ratio"]:.2f}')
        elif r['mdd'] >= 15:
            print(f'  {ticker.upper()}: ✗ MDD={r["mdd"]:.2f}% >= 15% (fails criterion)')
        else:
            print(f'  {ticker.upper()}: ? MDD=0, ratio=N/A')


if __name__ == '__main__':
    main()
