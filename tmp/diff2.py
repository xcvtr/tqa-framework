"""Тест контаминации: даёт ли предшествующий IS-прогон другой результат OOS.
Плюс проверка влияния итерации по всем дням (как self_tester) против только окна."""
import json
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.broker import BrokerSim
from strategies.trend_sma.engine import Config, State, force_close, run_days
from strategies.trend_sma.self_tester import load_specs
from scripts.sma_sweep import build_md, load_rates

TK, FAST, SLOW, MA = 'xauusd', 50, 60, 'sma'
END = '2026-11-30'
md = build_md(TK, '2019-10-01', END)
rates = load_rates('2019-10-01', END)


def run(dates, tag, slow=SLOW):
    cfg = Config(start=dates[0], eq_cap=200_000.0, specs=load_specs([TK]), fixed_lots=1.0,
                 rates=rates,
                 params_by_ticker={TK: {'sma_fast': FAST, 'sma_slow': slow, 'ma_type': MA}},
                 trailing_params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                  'activation': 2.0, 'trail': 1.5, 'oi_pyramid_max': 0},
                 broker=BrokerSim(commission=3.5, slippage_in=0.6, slippage_out=0.6))
    st = State(200_000.0)
    run_days(md, st, cfg, dates)
    force_close(md, st, cfg)
    pips = sum(t['pnl_quote'] for t in st.closed_trades) / 10.0
    print(f'{tag}: n={len(st.closed_trades)} пп={pips:.1f}', flush=True)
    return pips


oos = [d for d in md.dates if '2026-01-01' <= d <= END]
is_ = [d for d in md.dates if '2020-01-01' <= d <= '2024-12-31']

run(oos, 'OOS чистый')
run(is_, 'IS 2020-2024 (50/60)')
run(oos, 'OOS после IS 50/60')
run(is_, 'IS 2020-2024 (50/100)', slow=100)
run(oos, 'OOS после IS 50/100')
run(md.dates, 'OOS как self_tester (итерация по всем дням, start=2026)')
