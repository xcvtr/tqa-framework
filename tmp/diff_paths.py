"""Сравнение путей: sweep-путь (build_md c 2019) против self_tester-пути (warmup 150 дней).
Один конфиг xauusd 50/60sma, окно 2026-01-01..2026-11-30, фикс 1 лот."""
import json
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.broker import BrokerSim
from strategies.trend_sma.engine import Config, State, force_close, run_days
from strategies.trend_sma.self_tester import load_specs
from scripts.sma_sweep import build_md, load_rates

TK, FAST, SLOW, MA = 'xauusd', 50, 60, 'sma'
START, END = '2026-01-01', '2026-11-30'


def run(load_start, tag):
    md = build_md(TK, load_start, END)
    rates = load_rates(load_start, END)
    dates = [d for d in md.dates if START <= d <= END]
    cfg = Config(start=dates[0], eq_cap=200_000.0, specs=load_specs([TK]), fixed_lots=1.0,
                 rates=rates,
                 params_by_ticker={TK: {'sma_fast': FAST, 'sma_slow': SLOW, 'ma_type': MA}},
                 trailing_params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                  'activation': 2.0, 'trail': 1.5, 'oi_pyramid_max': 0},
                 broker=BrokerSim(commission=3.5, slippage_in=0.6, slippage_out=0.6))
    st = State(200_000.0)
    print(f'{tag}: дней в окне {len(dates)}, истории до старта {sum(1 for d in md.dates if d < START)}')
    run_days(md, st, cfg, dates)
    force_close(md, st, cfg)
    tr = st.closed_trades
    out = [{'ticker': t['ticker'], 'dir': t['direction'], 'entry_ts': t['entry_ts'],
            'entry': round(t['entry_price'], 3), 'exit': round(t['exit_price'], 3),
            'reason': t['reason'], 'pnl_quote': round(t['pnl_quote'], 2)} for t in tr]
    pips = sum(t['pnl_quote'] for t in tr) / 10.0
    print(f'{tag}: n={len(tr)} пп={pips:.1f}')
    json.dump(out, open(f'results/diff_{tag}.json', 'w'), ensure_ascii=False, indent=1)
    return out


a = run('2019-10-01', 'sweep')
b = run('2025-06-01', 'selftester_style')

print('\nрасхождения по входам:')
ka = {x['entry_ts']: x for x in a}
kb = {x['entry_ts']: x for x in b}
print(' только в sweep:', len(set(ka) - set(kb)), ' только в 150d:', len(set(kb) - set(ka)))
for ts in sorted(set(ka) & set(kb)):
    if ka[ts] != kb[ts]:
        print(' ', ts, 'sweep:', ka[ts]['exit'], ka[ts]['reason'], ka[ts]['pnl_quote'],
              '| 150d:', kb[ts]['exit'], kb[ts]['reason'], kb[ts]['pnl_quote'])
