"""Live/paper драйвер: ТО ЖЕ run_days, только данные приходят по мере поступления.

Механика:
1. На старте тянем историю (149 торговых дней) из CH → 5м-бары + список дат.
2. Держим снимок состояния на начало текущего дня (day-start snapshot).
3. Каждый тик: читаем из CH M1 текущего дня «до сейчас», пересобираем сегодняшние
   M1/5м-массивы, затем ПОВТОРЯЕМ текущий день с day-start snapshot через run_days.
4. Результат реплея — новое состояние (детерминированно, тот же код, что бэктест).

Почему реплей, а не инкремент: run_days — единственная реализация решений; повтор
дня с начала даёт ровно то же состояние, что и бэктест на этот момент, и не требует
второй копии логики (вход/пирамидинг/трейлинг/гейты).

Режимы:
  python -m strategies.trend_sma.live --tickers ... --replay 2026-11-03:2026-11-11
      прогнать прошлые дни «как в папере» и сравнить с бэктестом;
  python -m strategies.trend_sma.live --tickers ... --paper
      живой цикл по закрытым M1-барам.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import time
from datetime import datetime, timedelta as _td, timezone as _tz

_MSK = _tz(_td(hours=3))   # журнал пишем в МСК, а не в TZ хоста/контейнера

sys.path.insert(0, '/home/user/projects/tqa-m')

from strategies.trend_sma.engine import (  # noqa: E402
    Config, MarketData, State, msk_date, resample5, run_days,
)
from strategies.trend_sma.self_tester import load_specs  # noqa: E402

log = logging.getLogger("tqa_m.live")

_LOOKBACK_CAL_DAYS = 150


def _connector(args):
    """--connector mock → ExchangeMock (филл-цены через коннектор); none = как раньше."""
    if getattr(args, 'connector', 'none') == 'mock':
        from tqa_m.connector.exchange_mock import ExchangeMock
        return ExchangeMock()
    return None


def _signals(args):
    """--signals pg → сигналы из PG (их пишет контейнер детектора)."""
    if getattr(args, 'signals', 'local') == 'pg':
        from strategies.trend_sma.signals_pg import PGSignalSource
        return PGSignalSource(getattr(args, 'signals_schema', 'futures'))
    return None


def _ch_client():
    import clickhouse_connect
    return clickhouse_connect.get_client(host='10.0.0.60', port=8123, database='moex')


def _query_m1(tickers: list[str], start: str, end: str | None) -> dict[str, list[dict]]:
    """M1-бары по тикерам (canon-сырьё mt5_roll) — тот же загрузчик, что у бэктеста."""
    from strategies.trend_sma.data_provider import load_multiple_tickers
    return load_multiple_tickers(tickers, start, end)


def _pos_to_dict(pos) -> dict:
    d = dict(pos.__dict__)
    d.pop('pm', None)
    if d.get('entry_time') is not None:
        d['entry_time'] = str(d['entry_time'])
    return d


def _pos_from_dict(d: dict):
    from strategies.trend_sma.broker import Position
    pos = Position(ticker=d['ticker'], direction=d['direction'], entry_price=d['entry_price'],
                   entry_bar=d.get('entry_bar', 0), shares=d['shares'],
                   strategy=d.get('strategy', 'trend_sma'), step_price=d.get('step_price', 1.0),
                   min_step=d.get('min_step', 0.01), pct=d.get('pct', 1.0),
                   trailing_params=d.get('trailing_params') or {}, commission=d.get('commission', 4))
    for k, v in d.items():
        if k not in ('pm',) and hasattr(pos, k):
            setattr(pos, k, v)
    return pos


class LiveDB:
    """Журнал state в PG: восстановление при рестарте, запись сделок, редкий save.

    Коммит на каждый M1-бар запрещён (skill: yearly paper → минуты fsync).
    """

    def __init__(self, schema: str, eq0: float):
        import psycopg2
        from tqa_m.state.pg_state import PaperDB
        self.conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
        self.db = PaperDB(self.conn, schema)
        self.db.ensure(eq0)
        self._saved = 0
        self._n_trades = 0
        self.last_ts = 0

    def restore(self, st: State, specs: dict | None = None) -> State:
        row = self.db.load_state()
        if not row:
            return st
        st.equity, st.peak_equity = row['equity'], row['peak']
        self.last_ts = int(row.get('last_ts') or 0)
        st.open_pos = {tk: _pos_from_dict(p) for tk, p in row['positions'].items()
                       if isinstance(p, dict) and p.get('ticker')}
        # ГО по контракту берём из спеков (в позиции он не хранится) — иначе
        # после рестарта портфельный GO-гейт открыт и живой контур перебирает плечо.
        specs = specs or {}
        st.pos_go = {tk: float(specs.get(tk, {}).get('go', 0.0)) for tk in st.open_pos}
        st._restored_positions = len(st.open_pos)
        return st

    def sync(self, st: State, force: bool = False, every_s: int = 300) -> None:
        # новые закрытые сделки
        for tr in st.closed_trades[self._n_trades:]:
            self.db.save_trade({
                'strategy': 'trend_sma', 'symbol': tr['ticker'],
                'direction': 'LONG' if tr['direction'] == 'long' else 'SHORT',
                'pnl_rub': float(tr['pnl']),
                'ts_open': datetime.fromtimestamp(int(tr['entry_i']), tz=_MSK),
                'ts_close': datetime.fromtimestamp(int(tr['exit_i']), tz=_MSK),
                'entry_price': float(tr['entry_price']), 'exit_price': float(tr['exit_price']),
                'reason': tr['reason'], 'shares': int(tr['shares']),
            })
        self._n_trades = len(st.closed_trades)
        if not force and time.time() - self._saved < every_s:
            return
        self.db.save({'equity': float(st.equity), 'peak': float(st.peak_equity),
                      'balance': float(st.eq0), 'last_ts': int(self.last_ts or 0),
                      'positions': {tk: _pos_to_dict(p) for tk, p in st.open_pos.items()}})
        self._saved = time.time()


class LiveMarket(MarketData):
    """MarketData, у которого M1 держим только за текущий день, 5м — за всю историю."""

    def __init__(self):
        super().__init__()
        self.hist_b5: dict[str, tuple] = {}   # ticker -> (ts,o,h,l,p,v) за прошлые дни

    def load_history(self, tickers: list[str], load_start: str, today: str) -> None:
        self.load_history_bars(tickers, _query_m1(tickers, load_start, today))

    def load_history_bars(self, tickers: list[str], bars_by_tk: dict, before: str | None = None) -> None:
        """История = всё до `before` (для replay — день за днём; для paper — до запуска)."""
        self.hist_b5.clear(); self._tid.clear()
        for tk in tickers:
            bars = [b for b in bars_by_tk.get(tk, [])
                    if before is None or msk_date(b['ts']) < before]
            if not bars:
                log.warning(f"{tk}: нет истории")
                continue
            ts5, o5, h5, l5, p5, v5 = resample5(bars)
            self.hist_b5[tk] = (ts5, o5, h5, l5, p5, v5)
            self._tid[tk] = len(self._tid)
        self.dates = sorted({d for tk in self.hist_b5 for d in self._day_map(self.hist_b5[tk][0])})

    @staticmethod
    def _day_map(ts5) -> dict[str, tuple[int, int]]:
        from strategies.trend_sma.engine import _group_days
        return _group_days(ts5)

    def set_today(self, today: str, bars_by_tk: dict[str, list[dict]]) -> None:
        """Пересобрать представление данных на текущий момент."""
        for tk in self.hist_b5:
            today_bars = [b for b in bars_by_tk.get(tk, []) if msk_date(b['ts']) == today]
            hist_ts, hist_o, hist_h, hist_l, hist_p, hist_v = self.hist_b5[tk]
            ts5, o5, h5, l5, p5, v5 = resample5(today_bars) if today_bars else ([], [], [], [], [], [])
            self.m1_ts[tk] = [int(b['ts'].timestamp()) for b in today_bars]
            self.m1_hi[tk] = [b['hi'] for b in today_bars]
            self.m1_lo[tk] = [b['lo'] for b in today_bars]
            self.m1_prc[tk] = [b['prc'] for b in today_bars]
            self.m1_vol[tk] = [b['vol'] for b in today_bars]
            self.day_m1[tk] = ({today: (0, len(today_bars))} if today_bars else {})
            self.b5_ts[tk] = list(hist_ts) + list(ts5)
            self.b5_o[tk] = list(hist_o) + list(o5)
            self.b5_h[tk] = list(hist_h) + list(h5)
            self.b5_l[tk] = list(hist_l) + list(l5)
            self.b5_p[tk] = list(hist_p) + list(p5)
            self.b5_v[tk] = list(hist_v) + list(v5)
            self.day5[tk] = self._day_map(list(hist_ts) + list(ts5))
        self.dates = sorted({d for tk in self.day5 for d in self.day5[tk]})
        self._tid = {tk: n for n, tk in enumerate(self.b5_ts)}


def _last_closed_m1_ts(tickers: list[str]) -> int | None:
    client = _ch_client()
    try:
        q = "SELECT max(bt) FROM moex.mt5_roll WHERE ticker IN %(tickers)s"
        res = client.query(q, parameters={'tickers': tuple(tickers)})
        mx = res.result_rows[0][0]
        return int(mx.timestamp()) if mx else None
    finally:
        client.close()


def replay(tickers: list[str], date_from: str, date_to: str, args) -> list[dict]:
    """Прогнать прошлые дни «как папер»: по одному дню через run_days, без ретро-правок."""
    dates = []
    d0 = datetime.strptime(date_from, '%Y-%m-%d')
    d1 = datetime.strptime(date_to, '%Y-%m-%d')
    while d0 <= d1:
        dates.append(d0.strftime('%Y-%m-%d'))
        d0 += _td(days=1)

    load_start = (datetime.strptime(date_from, '%Y-%m-%d') - _td(days=_LOOKBACK_CAL_DAYS * 2)).strftime('%Y-%m-%d')
    md = LiveMarket()
    md.load_history(tickers, load_start, date_from)
    cfg = Config(start=date_from, risk_pct=args.risk_pct, go_knur=args.go_knur,
                 eq_cap=args.eq_cap, no_pyramid=args.no_pyramid, specs=load_specs(tickers),
                 connector=_connector(args), signal_source=_signals(args))
    st = State(float(args.eq_cap))

    all_bars = _query_m1(tickers, load_start, (datetime.strptime(date_to, '%Y-%m-%d') + _td(days=1)).strftime('%Y-%m-%d'))
    db = LiveDB(args.pg_schema, float(args.eq_cap)) if getattr(args, 'pg_schema', '') else None
    for day in dates:
        if not any(msk_date(b['ts']) == day for bars in all_bars.values() for b in bars):
            continue
        snap = copy.deepcopy(st)
        md.load_history_bars(tickers, all_bars, before=day)
        md.set_today(day, all_bars)
        st = run_days(md, snap, cfg, [day])
        log.info(f"{day}: equity={st.equity:,.0f} trades={len(st.closed_trades)}")
        if db is not None:
            db.sync(st, force=True)
    return st.closed_trades


def paper(tickers: list[str], args) -> None:
    """Живой цикл: каждые poll секунд — новый закрытый M1 → реплей текущего дня."""
    today = datetime.now().strftime('%Y-%m-%d')
    md = LiveMarket()
    load_start = (datetime.now() - _td(days=_LOOKBACK_CAL_DAYS * 2)).strftime('%Y-%m-%d')
    md.load_history(tickers, load_start, today)
    cfg = Config(start=(args.start or today), risk_pct=args.risk_pct, go_knur=args.go_knur,
                 eq_cap=args.eq_cap, no_pyramid=args.no_pyramid, specs=load_specs(tickers),
                 connector=_connector(args), signal_source=_signals(args))
    st = State(float(args.eq_cap))
    if args.pg_schema:
        db = LiveDB(args.pg_schema, float(args.eq_cap))
        st = db.restore(st, cfg.specs)
        go_used = sum(st.pos_go.get(t, 0.0) * p.shares for t, p in st.open_pos.items())
        log.info(f"State restored: equity={st.equity:,.0f} positions={list(st.open_pos)} "
                 f"go_used={go_used:,.0f}")
    else:
        db = None
    day_snapshot = copy.deepcopy(st)
    last_seen = int(getattr(db, 'last_ts', 0) or 0)
    last_ts = last_seen
    log.info(f"Paper loop: {len(tickers)} тикеров, poll {args.poll}s, schema={args.pg_schema or 'нет'}")
    while True:
        ts = _last_closed_m1_ts(tickers)
        if ts and ts > last_seen:
            last_seen = ts
            day = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            if day != today:
                log.info(f"Новый день {day}, фиксируем state")
                day_snapshot = copy.deepcopy(st)
                today = day
            bars = _query_m1(tickers, today, None)
            md.set_today(today, bars)
            # База: первый тик — снапшот начала дня (полный реплей), дальше — прошлый
            # state. Иначе min_ts отрежет уже пройденные бары, а снапшот их не содержит
            # → позиции, открытые утром, «исчезают» на следующем тике.
            base = st if last_ts else day_snapshot
            st = run_days(md, copy.deepcopy(base), cfg, [today], min_ts=last_ts)
            last_ts = ts
            log.info(f"{today} до {datetime.fromtimestamp(ts)}: equity={st.equity:,.0f} "
                     f"trades={len(st.closed_trades)} open={list(st.open_pos)}")
            if db is not None:
                db.last_ts = last_ts
                db.sync(st, every_s=args.save_every)
        time.sleep(args.poll)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='TQA-M live/paper (один движок с бэктестом)')
    ap.add_argument('--tickers', required=True)
    ap.add_argument('--replay', default='', help='YYYY-MM-DD:YYYY-MM-DD — прогон прошлого как папером')
    ap.add_argument('--paper', action='store_true', help='живой цикл')
    ap.add_argument('--poll', type=int, default=20)
    ap.add_argument('--risk-pct', type=float, default=0.025)
    ap.add_argument('--go-knur', type=float, default=8.0)
    ap.add_argument('--eq-cap', type=float, default=200_000)
    ap.add_argument('--no-pyramid', action='store_true')
    ap.add_argument('--start', default='', help='дата, с которой разрешены входы (default: сегодня)')
    ap.add_argument('--connector', choices=['none', 'mock'], default='none')
    ap.add_argument('--signals', choices=['local', 'pg'], default='local',
                    help='local = detect в процессе; pg = читать pending_signals')
    ap.add_argument('--signals-schema', default='futures')
    ap.add_argument('--pg-schema', default='', help='PG-схема журнала (пусто = без записи)')
    ap.add_argument('--save-every', type=int, default=300, help='сек между save state (не per-bar)')
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]

    if args.replay:
        a, b = args.replay.split(':')
        trades = replay(tickers, a, b, args)
        print(f"REPLAY trades={len(trades)} pnl={sum(t['pnl'] for t in trades):,.2f}")
        return
    if args.paper:
        paper(tickers, args)
        return
    ap.error('нужен --replay или --paper')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    main()
