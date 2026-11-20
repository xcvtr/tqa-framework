"""IR Strategy Backtest — per-ticker ROI/MDD metrics for FOREX CH data.

Usage:
    PYTHONPATH=. python -m scripts.ir_backtest --start 2026-01-01 --end 2026-11-19 \
        --tickers EURUSD,GBPUSD,USDJPY,AUDUSD --capital 500

Output: per-ticker ROI, MDD, total signals, ROI/MDD ratio, MDD < 15% filter.
"""

import argparse
import sys
sys.path.insert(0, '/home/user/projects/tqa-f')

from datetime import datetime

from strategies.trend_sma.data_provider import load_ticker_bars
from strategies.ir.detect import detect
from strategies.trend_sma.engine import run_days, Config, State, MarketData, force_close, mtm_mdd


def parse_args():
    parser = argparse.ArgumentParser(description='IR Strategy Per-Ticker Backtest')
    parser.add_argument('--start', required=True, help='Start date YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='End date YYYY-MM-DD')
    parser.add_argument('--tickers', required=True, help='Comma-separated list')
    parser.add_argument('--capital', type=float, default=500, help='Start capital USD')
    parser.add_argument('--no-commission', action='store_true', help='Disable commissions')
    parser.add_argument('--no-swap', action='store_true', help='Disable swaps')
    return parser.parse_args()


def run_ticker_backtest(ticker, start, end, capital):
    """Run IR backtest for single ticker."""
    
    # Load bars from CH
    bars = load_ticker_bars(ticker.lower(), start, end)
    n_bars = len(bars)
    
    results = {}
    
    for i_val in [12, 24, 36, 48]:
        # Get IR signals
        config_detect = {'i': i_val, 'w': 24, 'h': 12, 'symbol': ticker.lower()}
        signals = detect(bars, config_detect)
        n_sigs = len(signals)
        
        # Build MarketData
        md = MarketData()
        md.add_ticker(ticker.lower(), bars)
        md.finalize()
        
        # Config — 500 USD start, no commissions/swaps
        cfg = Config(
            start=start,
            risk_pct=0.025,
            go_knur=8.0,
            eq_cap=capital,
            no_pyramid=True,
            specs=None,
            connector=None,
            signal_source=None,
            params_by_ticker=None,
            fixed_lots=0,
            rates=None,
            trailing_params=None,
            dd_stop=None,
            dd_resume=None,
            dd_cool=None,
            sma_fast=20,
            sma_slow=60,
            ma_type='ema',
            window_days=150
        )
        
        st = State(capital)
        
        # Get unique dates from bars
        dates_set = set()
        for b in bars:
            dates_set.add(b['ts'].strftime('%Y-%m-%d'))
        dates_list = sorted(dates_set)
        
        try:
            result = run_days(md, st, cfg, dates_list)
            force_close(md, st, cfg)
            
            roi = (st.equity - capital) / capital * 100
            closed_trades = st.closed_trades
            
            # MTM MDD
            mdd = mtm_mdd(md, cfg, st.closed_trades, capital)[0]
            
            roi_mdd_ratio = roi / abs(mdd) if mdd != 0 and abs(mdd) > 0.01 else None
            
            # Count signals by direction
            long_signals = sum(1 for s in signals if s.get('tr', 0) == 1)
            short_signals = sum(1 for s in signals if s.get('tr', 0) == -1)
            
            mdd_less_15 = abs(mdd) < 15
            
            results[i_val] = {
                'roi': roi,
                'mdd': mdd,
                'total_signals': n_sigs,
                'long_signals': long_signals,
                'short_signals': short_signals,
                'roi_mdd_ratio': roi_mdd_ratio,
                'final_equity': st.equity,
                'num_trades': len(closed_trades) if closed_trades else 0,
                'mdd_less_15': mdd_less_15
            }
            
        except Exception as e:
            results[i_val] = {
                'error': str(e)[:100],
                'roi': None,
                'mdd': None,
                'total_signals': n_sigs
            }
    
    return results


def main():
    args = parse_args()
    
    tickers = [t.strip().lower() for t in args.tickers.split(',')]
    
    print('=' * 80)
    print('IR STRATEGY BACKTEST: Per-Ticker ROI/MDD Metrics')
    print(f'Start: {args.start}, End: {args.end}')
    print(f'Capital: {args.capital} USD')
    print('=' * 80)
    print()
    
    all_results = {}
    
    for ticker in tickers:
        print(f'Processing {ticker.upper()}...')
        results = run_ticker_backtest(ticker, args.start, args.end, args.capital)
        all_results[ticker] = results
        
        # Print summary for this ticker
        print(f'  i     ROI        MDD       Ratio    Signals  Trades  MDD<15')
        print(f'  ----  ---------  --------  --------  -------  ------  ------')
        
        for i_val in [12, 24, 36, 48]:
            r = results[i_val]
            if 'error' in r:
                print(f'  {i_val:>4} ERROR       {"-":>8} {"-":>8} {r["total_signals"]:>8} {"-":>6} {"-":>6}')
            else:
                roi_str = '{:+.2f}%'.format(r['roi'])
                mdd_str = '{:+.2f}'.format(r['mdd']) if r['mdd'] is not None else '-'
                ratio_str = '{:.2f}'.format(r['roi_mdd_ratio']) if r['roi_mdd_ratio'] is not None else 'N/A'
                mdd_15 = 'YES' if r.get('mdd_less_15', False) else 'NO'
                print(f'  {i_val:>4} {roi_str:>9} {mdd_str:>8} {ratio_str:>8} {r["total_signals"]:>8} {r.get("num_trades", 0):>6} {mdd_15:>6}')
        
        print()
    
    # Final summary
    print('=' * 80)
    print('FINAL SUMMARY')
    print('=' * 80)
    print(f"{'Ticker':>8} {'i':>4} {'ROI':>10} {'MDD':>10} {'Ratio':>10} {'Signals':>8} {'Trades':>6} {'MDD<15':>6}")
    print('-' * 55)
    
    total_signals_all = 0
    total_trades_all = 0
    tickers_passing_mdd = 0
    
    for ticker in tickers:
        for i_val in [12, 24, 36, 48]:
            r = all_results[ticker][i_val]
            if 'error' not in r:
                total_signals_all += r['total_signals']
                total_trades_all += r.get('num_trades', 0)
                if r.get('mdd_less_15', False):
                    tickers_passing_mdd += 1
                
                roi_val = r['roi']
                mdd_val = r['mdd']
                ratio_val = r['roi_mdd_ratio']
                
                roi_str = '{:+.2f}%'.format(roi_val) if roi_val is not None else 'N/A'
                mdd_str = '{:+.2f}'.format(mdd_val) if mdd_val is not None else 'N/A'
                ratio_str = '{:.2f}'.format(ratio_val) if ratio_val is not None else 'N/A'
                
                mdd_15 = 'YES' if r.get('mdd_less_15', False) else 'NO'
                
                line = ticker.rjust(8) + ' ' + str(i_val).rjust(4) + ' ' + roi_str.rjust(10) + ' ' + mdd_str.rjust(10) + ' ' + ratio_str.rjust(10) + ' ' + str(r['total_signals']).rjust(8) + ' ' + str(r.get('num_trades', 0)).rjust(6) + ' ' + mdd_15
                print(line)
    
    print()
    print(f'Total signals across all: {total_signals_all}')
    print(f'Total trades across all: {total_trades_all}')
    print(f'Tickers with MDD < 15%: {tickers_passing_mdd}/4')
    
    # Quality check
    print()
    print('QUALITY CHECK: ROI/MDD ratio, filtering MDD < 15%:')
    for ticker in tickers:
        print(f'  {ticker.upper()}:')
        best_ratio = -999
        best_i = None
        best_mdd = None
        for i_val in [12, 24, 36, 48]:
            r = all_results[ticker][i_val]
            if 'error' not in r and r.get('mdd_less_15', False) and r['roi_mdd_ratio'] is not None:
                if r['roi_mdd_ratio'] > best_ratio:
                    best_ratio = r['roi_mdd_ratio']
                    best_i = i_val
                    best_mdd = r['mdd']
        if best_i is not None:
            print(f'    Best ROI/MDD: {best_ratio:.2f} (i={best_i}), MDD={best_mdd:+.2f} ✓ MDD<15%')
        else:
            print(f'    No configs with MDD < 15% and valid ratio')


if __name__ == '__main__':
    main()
