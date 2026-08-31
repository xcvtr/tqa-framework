"""LSR-CROSS risk sweep: find max CAGR @ MTM MDD <= 20%"""
import sys; sys.path.insert(0, '.')
from tqa_framework.backtesters.lsr_cross import LsrCrossBacktester

TICKERS = 'BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT'.split(',')
PARAMS = {
    'z_score': 2.0, 'exclude_enabled': False,
    'exclude_hours': [], 'exclude_dow': [], 'exclude_syms': [],
    'sl_pct': 0.05, 'tp_pct': 0.18,
    'trail_act': 0.04, 'trail_dist': 0.03, 'trail_lock': 0.02,
    'hold_h': 120, 'pyr_trigger': 0.05, 'pyr_add': 0.5, 'pyr_max': 1,
}

print(f"{'risk':>6} | {'return':>10} | {'MDD':>8} | {'CAGR':>8} | {'trades':>6} | {'Calmar':>8}")
print('-' * 65)

results = []
for risk in [r/100 for r in range(8, 32, 2)]:
    bt = LsrCrossBacktester(
        tickers=[{'symbol': s} for s in TICKERS],
        days=1095, risk_pct=risk, tf_minutes=60,
        strategy_name='lsr_cross', strategy_params=PARAMS,
        initial_equity=1000.0, ch_host='http://10.0.0.60:8123',
        ch_db='crypto', max_conc=6,
    )
    r = bt.run()
    s = r['summary']
    eq = r.get('equity_curve', [])
    eq0 = 1000.0
    eqN = float(eq[-1]['equity']) if eq else eq0
    days = 1095
    cagr = (eqN / eq0) ** (365 / days) - 1 if eqN > 0 and eq0 > 0 else 0.0
    ret = s.get('total_return', 0)
    mdd = s.get('mdd', 0)
    trades = s.get('total_trades', 0)
    calmar = s.get('calmar_ratio', 0)
    results.append((risk, ret, mdd, cagr, trades, calmar))
    print(f"{risk:>5.2f} | {ret:>+8.2f}% | {mdd:>6.2f}% | {cagr:>+7.2%} | {trades:>5} | {calmar:>7.2f}")
    sys.stdout.flush()

print('\n===== BEST CAGR @ MDD ≤ 20% =====')
best = max([r for r in results if r[2] <= 20], key=lambda x: x[3], default=None)
if best:
    print(f"risk={best[0]:.2f} ret={best[1]:+.2f}% DD={best[2]:.2f}% CAGR={best[3]:+.2%} trades={best[4]} Calmar={best[5]:.1f}")
else:
    print("No config meets MDD <= 20%")