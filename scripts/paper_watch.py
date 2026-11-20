#!/usr/bin/env python3
"""MOEX tqa-f папер (два контейнера: loader + position) → Matrix #paper.

Алерт только при изменении: новый вход / закрытие / equity / сдохший контейнер /
протухшие данные. Пустой stdout = ничего не шлём. Состояние в файле.

Почему так: «контейнер Up» не значит «папер торгует». Если loader встал, roll
не пополняется и папер молча идёт по старому дню — это и ловим (freshness).
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    os.environ.pop(_k, None)
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '*'

import psycopg2  # noqa: E402

STATE_FILE = os.path.expanduser('~/.hermes/state/tqa_f_paper_watch.json')
SCHEMA = 'tqa_f_paper'
CH = 'http://10.0.0.60:8123'


def ch_query(sql: str) -> str:
    req = urllib.request.Request(CH + '/?' + urllib.parse.urlencode({'query': sql + ' FORMAT TSV'}))
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode().strip()


def container_state(name: str) -> str:
    try:
        out = subprocess.run(['docker', 'inspect', '-f', '{{.State.Status}}', name],
                             capture_output=True, text=True, timeout=8)
        return out.stdout.strip() or 'missing'
    except Exception:
        return 'unknown'


def read_state() -> dict:
    c = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex',
                         user='postgres', connect_timeout=10)
    cur = c.cursor()
    cur.execute(f"SELECT equity, peak, positions, updated_at FROM {SCHEMA}.multi_state")
    eq, peak, pos, upd = cur.fetchone()
    syms = {}
    detail = {}
    for tk, p in (pos or {}).items():
        d = str(p.get('direction', '?'))
        syms[tk] = d[:1].upper()
        detail[tk] = {'dir': d, 'shares': float(p.get('shares') or 0),
                      'entry': float(p.get('entry_price') or 0),
                      'step': float(p.get('step_price') or 0),
                      'min_step': float(p.get('min_step') or 0) or 1e-4,
                      'pct': float(p.get('pct') or 1),
                      'comm': float(p.get('commission') or 0)}
    cur.execute(f"SELECT count(*), coalesce(sum(pnl_rub), 0) FROM {SCHEMA}.multi_closed_trades")
    n_closed, pnl = cur.fetchone()
    cur.execute(f"SELECT max(ts_close) FROM {SCHEMA}.multi_closed_trades")
    last_close = cur.fetchone()[0]
    c.close()

    # unrealized: та же формула, что в broker._close_market (ticks * step * shares * pct − комиссия)
    unrlz = {}
    total_unrlz = 0.0
    if detail:
        tk_list = ','.join("'" + t + "'" for t in detail)
        rows = ch_query(f'SELECT ticker, argMax(prc, bt) FROM moex.mt5_roll '
                        f'WHERE ticker IN ({tk_list}) GROUP BY ticker')
        px = {}
        for ln in rows.splitlines():
            if not ln.strip():
                continue
            tk, v = ln.split('\t')
            px[tk] = float(v)
        for tk, d in detail.items():
            px_tk = px.get(tk)
            if px_tk is None or not d['entry'] or not d['step']:
                continue
            ticks = (px_tk - d['entry']) / d['min_step']
            if d['dir'] == 'short':
                ticks = -ticks
            pnl_tk = ticks * d['step'] * d['shares'] * d['pct'] - d['comm'] * d['shares']
            unrlz[tk] = {'pnl': round(pnl_tk, 2), 'px': px_tk, **d}
            total_unrlz += pnl_tk

    roll_last = ch_query('SELECT toString(max(bt)) FROM moex.mt5_roll')
    lag_days = ch_query(
        'SELECT toString(dateDiff(\'day\', max(bt), now())) FROM moex.mt5_roll')
    return {
        'eq': float(eq), 'peak': float(peak), 'open': len(syms), 'syms': syms,
        'closed': int(n_closed), 'pnl': float(pnl),
        'last_close': str(last_close) if last_close else '',
        'upd': str(upd) if upd else '',
        'roll_last': roll_last, 'lag_days': int(lag_days or 0),
        'c_paper': container_state('tqa-f-paper'),
        'c_loader': container_state('tqa-f-loader'),
        'unrlz': {k: {'pnl': round(v['pnl'], 2), 'px': v['px'], 'dir': v['dir'],
                      'shares': v['shares'], 'entry': v['entry']} for k, v in unrlz.items()},
        'u_total': round(total_unrlz, 2),
    }


def main() -> None:
    new = read_state()
    try:
        old = json.load(open(STATE_FILE))
    except Exception:
        old = None
    json.dump(new, open(STATE_FILE, 'w'))

    if old is None:
        return

    lines = []
    old_syms, new_syms = old.get('syms', {}), new['syms']
    opened = sorted(set(new_syms) - set(old_syms))
    closed = sorted(set(old_syms) - set(new_syms))

    du = new['u_total'] - old.get('u_total', 0)
    moved = abs(du) >= 2000
    if opened or closed or new['closed'] != old.get('closed') or moved:
        hdr = (f"📊 **tqa-f папер:** open={new['open']} eq={new['eq']:,.0f}₽ "
               f"peak={new['peak']:,.0f}₽ закрыто={new['closed']} (PnL {new['pnl']:+,.0f}₽)")
        if new['open']:
            hdr += f" | MTM {new['eq'] + new['u_total']:,.0f}₽ (незакр. {new['u_total']:+,.0f}₽)"
        lines.append(hdr)
    if opened:
        lines.append("   🟢 входы: " + ", ".join(f"{s}({'long' if new_syms[s]=='L' else 'short'})" for s in opened))
    if closed:
        lines.append("   🔴 вышли: " + ", ".join(closed))
    if lines:  # позиции печатаем только вместе с алертом, иначе тишина
        u = new.get('unrlz') or {}
        for tk in sorted(u, key=lambda k: -abs(u[k]['pnl'])):
            d = u[tk]
            lines.append(f"   {tk:<8} {d['dir']:<5} {d['shares']:>4.0f} @{d['entry']:g} → {d['px']:g}: {d['pnl']:+,.0f}₽")
        if u:
            lines.append(f"   итого незакрытый: {new['u_total']:+,.0f}₽")
    if not opened and not closed and new['eq'] != old.get('eq'):
        lines.append(f"📊 **tqa-f папер:** eq {old.get('eq'):,.0f}₽ → {new['eq']:,.0f}₽ "
                     f"(open={new['open']}, закрыто={new['closed']})")

    # здоровье: контейнеры и свежесть данных
    bad = []
    if new['c_paper'] != 'running':
        bad.append(f"position={new['c_paper']}")
    if new['c_loader'] != 'running':
        bad.append(f"loader={new['c_loader']}")
    if new['lag_days'] > 1:
        bad.append(f"данные отстали на {new['lag_days']} дн (roll до {new['roll_last'][:16]})")
    if bad and (not old or bad != old.get('_bad', [])):
        lines.append("⚠️ " + "; ".join(bad))
    new['_bad'] = bad
    json.dump(new, open(STATE_FILE, 'w'))

    if lines:
        print('\n'.join(lines))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"⚠️ TQA-M-PAPER-WATCH ERR: {str(e)[:120]}")
