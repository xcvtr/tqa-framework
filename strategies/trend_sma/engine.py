"""Один движок для test и paper/live.

Разница только в драйвере:
- test:   MarketData заполняется историей CH целиком, run_days(md, st, md.dates);
- paper:  MarketData заполняется скользящим окном (последние ~150 дней) на каждый
          новый закрытый день, run_days(md, st, [today]).
Логика решений (детект, сайзинг, гейты, тик позиции) — ровно одна и та же функция.

Данные держим в numpy-массивах (dict-бары 1.7M съедали 4ГБ и роняли прогон в OOM).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta as _td

import numpy as np

from tqa_f.data.bars import group_days as _group_days, utc_date, resample5  # noqa: F401

log = logging.getLogger("tqa_f.engine")

_TF_MIN = 5  # detect_minutes канона (tf=5)
_DBG = bool(os.environ.get('TQA_DBG'))

# Кластеры канона (risk.py): не более 1 открытой позиции на кластер
CLUSTERS = {'metal_index': {'MX', 'MM', 'RN', 'LK', 'RI', 'IMOEXF'},
            'fx': {'EURRUBF', 'CNYRUBF', 'USDRUBF', 'CR'}}


def cluster_of(tk):
    for _n, _m in CLUSTERS.items():
        if tk in _m:
            return _n
    return None


class MarketData:
    """M1 + 5м + разбивка по дням. Заполняется либо историей, либо окном."""

    def __init__(self):
        self.m1_ts: dict[str, np.ndarray] = {}
        self.m1_hi: dict[str, np.ndarray] = {}
        self.m1_lo: dict[str, np.ndarray] = {}
        self.m1_prc: dict[str, np.ndarray] = {}
        self.m1_vol: dict[str, np.ndarray] = {}
        self.day_m1: dict[str, dict[str, tuple[int, int]]] = {}
        self.b5_ts: dict[str, list] = {}
        self.b5_o: dict[str, np.ndarray] = {}
        self.b5_h: dict[str, np.ndarray] = {}
        self.b5_l: dict[str, np.ndarray] = {}
        self.b5_p: dict[str, np.ndarray] = {}
        self.b5_v: dict[str, np.ndarray] = {}
        self.day5: dict[str, dict[str, tuple[int, int]]] = {}
        self.dates: list[str] = []
        self._tid: dict[str, int] = {}

    # ── загрузка ──
    def add_ticker(self, ticker: str, bars) -> None:
        mts = [b['ts'] for b in bars]
        self.m1_ts[ticker] = np.array([int(t.timestamp()) for t in mts], dtype='int64')
        self.m1_hi[ticker] = np.array([b['hi'] for b in bars], dtype='float64')
        self.m1_lo[ticker] = np.array([b['lo'] for b in bars], dtype='float64')
        self.m1_prc[ticker] = np.array([b['prc'] for b in bars], dtype='float64')
        self.m1_vol[ticker] = np.array([b.get('vol', 0.0) for b in bars], dtype='float64')
        self.day_m1[ticker] = _group_days(mts)

        ts5, o5, h5, l5, p5, v5 = resample5(bars)
        self.b5_ts[ticker], self.b5_o[ticker], self.b5_h[ticker] = ts5, o5, h5
        self.b5_l[ticker], self.b5_p[ticker], self.b5_v[ticker] = l5, p5, v5
        self.day5[ticker] = _group_days(ts5)
        self._tid[ticker] = len(self._tid)

    def finalize(self) -> None:
        self.dates = sorted({d for tk in self.day5 for d in self.day5[tk]})
        self._tid = {tk: n for n, tk in enumerate(self.b5_ts)}

    @property
    def tickers(self) -> list[str]:
        return list(self.b5_ts)

    def bar_dict(self, tk: str, i: int) -> dict:
        return {'ts': self.b5_ts[tk][i], 'open': float(self.b5_o[tk][i]), 'high': float(self.b5_h[tk][i]),
                'low': float(self.b5_l[tk][i]), 'close': float(self.b5_p[tk][i]),
                'vol': float(self.b5_v[tk][i])}


class State:
    """Всё, что переживает день и рестарт контейнера."""

    def __init__(self, eq0: float):
        self.eq0 = float(eq0)
        self.equity = float(eq0)
        self.peak_equity = float(eq0)
        self.max_dd = 0.0
        self.total_signals = 0
        self.regime_blocked = 0
        self.dd_blocked_count = 0
        self.open_pos: dict = {}
        self.pos_go: dict[str, float] = {}
        self.detect_done: dict[str, str] = {}
        self.closed_trades: list[dict] = []


class SignalView:
    """Минимальный сигнал, который нужен движку (совместим с detect.Signal и строкой PG)."""
    __slots__ = ('direction', 'price')

    def __init__(self, direction: str, price: float):
        self.direction = direction
        self.price = float(price)


class Config:
    def __init__(self, *, start: str, risk_pct=0.025, go_knur=8.0, eq_cap=200_000.0,
                 no_pyramid=False, specs=None, broker=None, sma_fast=20, sma_slow=60,
                 ma_type='ema', window_days=150, connector=None, signal_source=None,
                 params_by_ticker=None, fixed_lots=0, rates=None, trailing_params=None, lot_step=0.0,
                 regime_er=0.0, regime_metric='er', regime_op='>=', regime_thr=None,
                 dd_stop=None, dd_resume=None, dd_cool=None):
        self.start = start
        self.dd_stop = dd_stop                    # доля: стоп новых входов при просадке от пика
        self.dd_resume = dd_resume if dd_resume is not None else (
            (dd_stop or 0) * 0.5)                 # возврат торговли при восстановлении до этого уровня
        self.dd_cool = dd_cool                    # торговых дней паузы, после которых торговля возвращается принудительно
        self.regime_er = float(regime_er or 0.0)          # >0 = запрет НОВЫХ входов при ER ниже порога
        self.regime_metric = regime_metric
        self.regime_op = regime_op
        self.regime_thr = regime_thr
        self.risk_pct = risk_pct
        self.go_knur = go_knur
        self.eq_cap = eq_cap
        self.sma_fast, self.sma_slow, self.ma_type = sma_fast, sma_slow, ma_type
        self.window_days = window_days
        self.specs = specs or {}
        self.params_by_ticker = params_by_ticker or {}   # {ticker: {sma_fast, sma_slow, ma_type}}
        self.fixed_lots = fixed_lots                      # >0 = фикс. лот, без сайзинга по equity
        self.lot_step = float(lot_step or 0.0)            # >0 = дробный сайзинг (FX), кратный шагу
        self.rates = rates or {}                          # {quote_ccy: (ts_array, price_array)} для USD-конверсии
        self.trailing_params = trailing_params or {
            'trail_mode': 'trend_sma', 'stop_loss': 1.5, 'activation': 2.0, 'trail': 1.5,
            'oi_pyramid_add': 1.0, 'oi_pyramid_thr': 0.015, 'oi_pyramid_max': 1 if no_pyramid else 6,
        }
        self.connector = connector
        self.signal_source = signal_source   # None = локальный detect; иначе PG
        if broker is None:
            from strategies.trend_sma.broker import BrokerSim
            broker = BrokerSim(commission=4, slippage_out=1, slippage_in=1, connector=connector)
        self.broker = broker


def _fx_usd(cfg: Config, ticker: str, ts: int) -> float:
    """Множитель PnL из валюты котировки в USD (курс на момент выхода)."""
    from tqa_f.data.forex_specs import SPECS, to_usd
    q = SPECS.get(ticker, {}).get('quote', 'USD')
    if q == 'USD':
        return 1.0
    r = cfg.rates.get(q)
    if r is None:
        return 1.0
    ts_arr, px = r
    if len(ts_arr) == 0:
        return 1.0
    j = int(np.searchsorted(ts_arr, ts, 'right')) - 1
    return to_usd(q, float(px[max(j, 0)]))


def er_series(closes, n=50):
    """ER Кауфмана по ряду дневных close: |C[i]-C[i-n]| / sum|dC|. 0..1."""
    out = []
    for i in range(len(closes)):
        if i < n:
            out.append(0.0)
            continue
        move = abs(closes[i] - closes[i - n])
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
        out.append(move / path if path else 0.0)
    return out


def regime_series(name, cl, hi, lo, op=None):
    """Ряд метрики режима по дневным close/high/low. Индексация — как в cl."""
    import math
    n = len(cl)
    out = [None] * n

    def tr(j):
        if j == 0:
            return hi[j] - lo[j]
        return max(hi[j] - lo[j], abs(hi[j] - cl[j - 1]), abs(lo[j] - cl[j - 1]))

    for i in range(n):
        if name == 'er':
            if i >= 50:
                path = sum(abs(cl[j] - cl[j - 1]) for j in range(i - 49, i + 1))
                out[i] = abs(cl[i] - cl[i - 50]) / path if path else 0.0
        elif name == 'chop':
            w = 14
            if i >= w:
                s = sum(tr(j) for j in range(i - w + 1, i + 1))
                rng = max(hi[i - w + 1:i + 1]) - min(lo[i - w + 1:i + 1])
                out[i] = 100 * math.log10(s / rng) / math.log10(w) if rng > 0 and s > 0 else None
        elif name in ('vr5', 'vr10'):
            q, win = (5 if name == 'vr5' else 10), 120
            if i >= win:
                r = [math.log(cl[j] / cl[j - 1]) for j in range(i - win + 1, i + 1)]
                m = sum(r) / len(r)
                v1 = sum((v - m) ** 2 for v in r) / len(r)
                if v1 > 0:
                    qr = [sum(r[k:k + q]) for k in range(len(r) - q + 1)]
                    mq = sum(qr) / len(qr)
                    vq = sum((v - mq) ** 2 for v in qr) / len(qr)
                    out[i] = vq / (q * v1)
        elif name == 'r2':
            w = 60
            if i >= w:
                ys = [math.log(cl[j]) for j in range(i - w + 1, i + 1)]
                xs = list(range(len(ys)))
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                sxx = sum((x - mx) ** 2 for x in xs)
                syy = sum((y - my) ** 2 for y in ys)
                out[i] = (sxy * sxy) / (sxx * syy) if sxx > 0 and syy > 0 else None
        elif name == 'sig':
            w = 100
            if i >= w:
                r = [math.log(cl[j] / cl[j - 1]) for j in range(i - 99, i + 1)]
                m = sum(r) / len(r)
                s_all = (sum((v - m) ** 2 for v in r) / len(r)) ** 0.5
                r20 = r[-20:]
                m20 = sum(r20) / len(r20)
                s20 = (sum((v - m20) ** 2 for v in r20) / len(r20)) ** 0.5
                out[i] = s20 / s_all if s_all > 0 else None
        elif name == 'ac1':
            w = 60
            if i >= w:
                r = [math.log(cl[j] / cl[j - 1]) for j in range(i - w + 1, i + 1)]
                m = sum(r) / len(r)
                num = sum((r[k] - m) * (r[k + 1] - m) for k in range(len(r) - 1))
                den = sum((v - m) ** 2 for v in r)
                out[i] = num / den if den else None
        elif name == 'body':                      # «телесность» дня: тело/диапазон, среднее за 20 дней
            w = 20
            if i >= w and op is not None:
                vals = [abs(cl[j] - op[j]) / (hi[j] - lo[j])
                        for j in range(i - w + 1, i + 1) if hi[j] > lo[j]]
                out[i] = sum(vals) / len(vals) if vals else None
        elif name == 'fb':                        # ложные пробои за 20 дней: пробил экстремум и вернулся
            w = 20
            if i >= 2 * w:
                cnt = 0
                for j in range(i - w + 1, i + 1):
                    ph = max(hi[j - w:j]); pl = min(lo[j - w:j])
                    if (hi[j] > ph or lo[j] < pl) and pl <= cl[j] <= ph:
                        cnt += 1
                out[i] = cnt / w
        elif name == 'trendz':
            w = 50
            if i >= w:
                r = [math.log(cl[j] / cl[j - 1]) for j in range(i - w + 1, i + 1)]
                m = sum(r) / len(r)
                sd = (sum((v - m) ** 2 for v in r) / len(r)) ** 0.5
                out[i] = abs(cl[i] - cl[i - w]) / (sd * math.sqrt(w) * cl[i]) if sd > 0 else None
    return out


def run_days(md: MarketData, st: State, cfg: Config, dates: list[str],
             min_ts: int = 0) -> State:
    """Прогнать указанные торговые дни. Мутирует st. Единственная реализация логики.

    min_ts > 0 — события не старше этого бара: после рестарта папер не переигрывает
    уже пройденную часть дня (иначе дубли закрытых сделок и фантомные выходы).
    """
    from strategies.trend_sma.position_manager import (size_position as _size_position,
                                                       check_portfolio_gates as _check_portfolio_gates,
                                                       frac_size_position, fx_size_position)
    from strategies.trend_sma.broker import Position

    broker = cfg.broker
    SLOW_DAYS = max([60] + [int(v.get('sma_slow', 0)) for v in cfg.params_by_ticker.values()]
                    + [int(cfg.sma_slow)])
    date_pos = {d: n for n, d in enumerate(md.dates)}

    # Гейт режима: ER Кауфмана по дневным close (5м-бар, закрывающий день).
    # 0..1 = насколько путь прямой: тренд близко к 1, боковик близко к 0.
    regime = None
    if cfg.regime_thr is not None:
        metric = cfg.regime_metric
        regime = {}
        for tk in md.day5:
            ds = sorted(md.day5[tk])
            cl, hi, lo, op_ = [], [], [], []
            for d in ds:
                a, b = md.day5[tk][d]
                cl.append(float(md.b5_p[tk][b - 1]))
                hi.append(float(max(md.b5_h[tk][a:b])))
                lo.append(float(min(md.b5_l[tk][a:b])))
                op_.append(float(md.b5_o[tk][a]))
            regime[tk] = dict(zip(ds, regime_series(metric, cl, hi, lo, op_)))
        log.info(f"Гейт режима: {metric} {cfg.regime_op} {cfg.regime_thr}")

    dd_blocked = False
    for current_date in dates:
        day_idx = date_pos[current_date]
        if day_idx % 200 == 0:
            log.info(f"Day {day_idx + 1}/{len(md.dates)}: {current_date}")

        # ── Стоп по просадке счёта: пик обновляем на закрытых сделках прошлых дней ──
        if cfg.dd_stop:
            st.peak_equity = max(st.peak_equity, st.equity)
            _dd = (st.peak_equity - st.equity) / st.peak_equity if st.peak_equity > 0 else 0.0
            if not dd_blocked and _dd >= cfg.dd_stop:
                dd_blocked = True
                _dd_day = day_idx
                log.info(f"{current_date}: СТОП по просадке {_dd*100:.1f}% ≥ {cfg.dd_stop*100:.0f}%")
            elif dd_blocked and (_dd <= cfg.dd_resume
                                 or (cfg.dd_cool and day_idx - _dd_day >= cfg.dd_cool)):
                dd_blocked = False
                log.info(f"{current_date}: торговля возобновлена, просадка {_dd*100:.1f}%")

        # ── События дня: ОБЪЕДИНЁННЫЙ M1-клок по всем тикерам (как канон engine.all_ts) ──
        ev_ts, ev_tk, ev_i = [], [], []
        entry_ts_day: dict[str, int] = {}
        for ticker in md.b5_ts:
            rm = md.day_m1[ticker].get(current_date)
            if rm is None:
                continue
            r5f = md.day5[ticker].get(current_date)
            if r5f is not None and r5f[1] > r5f[0]:
                _k = r5f[0] + 1 if r5f[1] - r5f[0] >= 2 else r5f[0]
                entry_ts_day[ticker] = int(md.b5_ts[ticker][_k].timestamp())
            ev_ts.append(md.m1_ts[ticker][rm[0]:rm[1]])
            ev_tk.append(np.full(rm[1] - rm[0], md._tid[ticker], dtype='int32'))
            ev_i.append(np.arange(rm[0], rm[1]))
        if not ev_ts:
            continue
        _ets = np.concatenate(ev_ts); _etk = np.concatenate(ev_tk); _ei = np.concatenate(ev_i)
        _ord = np.lexsort((_etk, _ets))
        entry_tried: set[str] = set()   # канон: pending исполняется на ПЕРВОМ доступном баре

        for _oi in _ord:
            ticker = md.tickers[int(_etk[_oi])]
            i = int(_ei[_oi]); ts_i = int(_ets[_oi])
            if ts_i <= min_ts:
                continue

            # ── TICK: позиция тикается на каждом своём M1-баре ──
            pos = st.open_pos.get(ticker)
            if pos is not None:
                _sk = int(pos._skip_ts) if pos._skip_ts is not None else 0
                if ts_i > _sk and broker.pm._update_trend_sma(pos, 0, md.m1_hi[ticker][i],
                                                              md.m1_lo[ticker][i], md.m1_prc[ticker][i],
                                                              md.m1_vol[ticker][i]) != 0:
                    _fx = _fx_usd(cfg, pos.ticker, ts_i)
                    # своп за ночи удержания (пункты за ночь × стоимость пункта × лот)
                    _spec = cfg.specs.get(pos.ticker, {})
                    _nights = max(0, (datetime.fromtimestamp(ts_i).date()
                                      - pos.entry_time.date()).days)
                    _swap_pips = float(_spec.get('swap_l' if pos.direction == 'long' else 'swap_s', 0.0))
                    _swap_q = _swap_pips * float(_spec.get('sp', 0.0)) * float(pos.shares) * _nights
                    _swap_usd = _swap_q * _fx
                    pos.pnl = pos.pnl + _swap_q
                    st.closed_trades.append({
                        'ticker': pos.ticker, 'direction': pos.direction,
                        'entry_price': pos.entry_price, 'exit_price': pos.exit_price,
                        'shares': pos.shares, 'pnl': pos.pnl * _fx, 'pnl_quote': pos.pnl,
                        'reason': pos.exit_reason,
                        'entry_ts': str(pos.entry_time), 'entry_i': _sk, 'exit_i': ts_i,
                        'nights': _nights, 'swap': round(_swap_usd, 4),
                    })
                    st.equity += pos.pnl * _fx
                    del st.open_pos[ticker]
                continue

            # ── ENTRY: первый M1 ВТОРОГО 5м-бара дня, детект — по close первого бара ──
            if current_date < cfg.start or day_idx < SLOW_DAYS - 1:
                continue
            _ent = entry_ts_day.get(ticker)
            if _ent is None or ts_i < _ent or ts_i >= _ent + 300 or ticker in entry_tried:
                continue
            entry_tried.add(ticker)
            if ticker in st.open_pos:
                if _DBG: print(f"DBG {current_date} {ticker}: skip detect (pos open)", flush=True)
                continue
            if st.detect_done.get(ticker) == current_date:
                if _DBG: print(f"DBG {current_date} {ticker}: detect already done", flush=True)
                continue
            r5 = md.day5[ticker].get(current_date)
            if r5 is None:
                continue
            if dd_blocked:
                st.dd_blocked_count = getattr(st, 'dd_blocked_count', 0) + 1
                continue                      # просадка счёта выше лимита: новых входов нет
            if regime is not None:
                _v = regime.get(ticker, {}).get(current_date)
                _ok = _v is not None and ((_v >= cfg.regime_thr) if cfg.regime_op == '>='
                                          else (_v <= cfg.regime_thr))
                if not _ok:
                    st.regime_blocked = getattr(st, 'regime_blocked', 0) + 1
                    continue                  # режим не подходит: новые входы запрещены
            # Канон: _trend_bar_cache держит ~150 дневных баров → detect видит последние 149
            # закрытых дней (+ текущий формирующийся). Если sma_slow больше — окно расширяем,
            # иначе detect никогда не наберёт slow закрытий.
            # бары в окне группируются по UTC-дате и часть дней схлопывается,
            # поэтому >149 дней нужно брать с запасом, иначе detect не наберёт slow
            _win = 149 if SLOW_DAYS <= 144 else int((SLOW_DAYS + 5) * 1.5)
            sig_bars = [md.bar_dict(ticker, _rr[1] - 1)
                        for _rr in (md.day5[ticker].get(d2) for d2 in md.dates[:day_idx])
                        if _rr is not None][-_win:]
            if r5[1] > r5[0]:
                sig_bars.append(md.bar_dict(ticker, r5[0]))          # forming first bar

            if cfg.signal_source is None:
                # Determine which detector to use based on strategy or default to trend_sma
                strategy = cfg.params_by_ticker.get(ticker, {}).get('strategy', 'trend_sma')
                if strategy == 'ir':
                    from strategies.ir.detect import detect as strategy_detect
                else:
                    from strategies.trend_sma.detect import detect as strategy_detect  # only for local-mode
                
                _tp = cfg.params_by_ticker.get(ticker, {})
                config = {'symbol': ticker,
                          'sma_fast': _tp.get('sma_fast', cfg.sma_fast),
                          'sma_slow': _tp.get('sma_slow', cfg.sma_slow),
                          'ma_type': _tp.get('ma_type', cfg.ma_type),
                          'window_days': cfg.window_days}
                signals = [SignalView(s.direction, s.price) for s in strategy_detect(sig_bars, config)]
            else:
                # Детектор вынесен (свой контейнер): сигналы читаем из PG.
                signals = cfg.signal_source.get(ticker, current_date)
            if _DBG: print(f"DBG {current_date} {ticker}: signals={len(signals)}", flush=True)
            for sig in signals:
                direction = 'long' if sig.direction == 'LONG' else ('short' if sig.direction == 'SHORT' else 'long')
                spec = cfg.specs.get(ticker, {'go': 10000.0, 'ms': 1.0, 'sp': 1.0, 'fee': 4.0})
                go_limit_tk = spec['go'] or 10000.0
                fee_entry = spec['fee']

                entry_price = float(sig.price)
                if entry_price <= 0 or ticker in st.open_pos:
                    continue
                st.detect_done[ticker] = current_date

                # входной слиппедж: плохая сторона на slippage_in тиков (раньше не применялся)
                _slip_in = float(getattr(broker, 'slippage_in', 0) or 0) * float(spec['ms'])
                # спред AlfaForex: половина снимается на входе, половина — на выходе (broker)
                _half_spread = float(spec.get('spread', 0.0) or 0.0) / 2.0 * float(spec['ms'])
                _cost_in = _slip_in + _half_spread
                if _cost_in:
                    entry_price += _cost_in if direction == 'long' else -_cost_in

                # ГО по спеке AlfaForex: маржа × нотионал (золото 6%, валюты 2.5%)
                _m = float(spec.get('margin', 0.0) or 0.0)
                if _m and entry_price > 0 and spec.get('ms'):
                    _contract = float(spec['sp']) / float(spec['ms'])   # лот в единицах базовой валюты
                    go_limit_tk = _m * _contract * entry_price
                base_eq = min(st.equity, cfg.eq_cap) if cfg.eq_cap > 0 else st.equity
                if cfg.fixed_lots:
                    lot_size = float(cfg.fixed_lots)
                elif cfg.lot_step > 0:
                    if _m and spec.get('ms'):
                        # FX: риск на стопе (а не ГО-кнут MOEX), сверху ограничение маржой
                        _contract0 = float(spec['sp']) / float(spec['ms'])
                        _sl_pct = float(cfg.trailing_params.get('stop_loss', 1.5))
                        _risk_per_lot = _sl_pct / 100.0 * entry_price * _contract0
                        lot_size = fx_size_position(base_eq, cfg.risk_pct, _risk_per_lot,
                                                    go_limit_tk, cfg.lot_step)
                    else:
                        lot_size = frac_size_position(base_eq, cfg.risk_pct, cfg.go_knur,
                                                      go_limit_tk, cfg.lot_step)
                else:
                    lot_size = _size_position(base_eq, cfg.risk_pct, cfg.go_knur, go_limit_tk)

                # Гейт ликвидности канона: vol первого M1 второго 5м-бара, shares/vol > 0.5 → отказ
                _v = float(md.m1_vol[ticker][i])
                if _v > 0 and lot_size / _v > 0.5:
                    if _DBG: print(f"DBG {current_date} {ticker}: REJ liq sh={lot_size} vol={_v}", flush=True)
                    continue

                # ── Портфельные гейты канона ──
                go_used = sum(st.pos_go.get(p.ticker, 0.0) * p.shares
                              for p in st.open_pos.values() if not p.closed)
                _ok, _why = _check_portfolio_gates(
                    ticker, lot_size, go_limit_tk, go_used, st.equity, cfg.go_knur,
                    list(st.open_pos.values()), cluster_of)
                if not _ok:
                    if _DBG and _why in ('go', 'go_reserve'):
                        print(f"DBG {current_date} {ticker}: REJ {_why} {go_used}+{go_limit_tk * lot_size} > {st.equity * cfg.go_knur}", flush=True)
                    continue

                _edt = datetime.fromtimestamp(ts_i)
                pos = Position(ticker=ticker, direction=direction, entry_price=entry_price,
                               entry_bar=len(sig_bars), shares=lot_size, strategy='trend_sma',
                               step_price=spec['sp'], min_step=spec['ms'], pct=1.0,
                               trailing_params=dict(cfg.trailing_params), commission=fee_entry)
                pos.spread_half = _half_spread
                pos.entry_time = _edt
                pos._skip_ts = ts_i
                st.pos_go[ticker] = go_limit_tk
                st.open_pos[ticker] = pos
                st.total_signals += 1
    return st


def force_close(md: MarketData, st: State, cfg: Config) -> None:
    for ticker, pos in list(st.open_pos.items()):
        last_close = float(md.b5_p[ticker][-1]) if len(md.b5_p[ticker]) else 0
        if last_close:
            _fx = _fx_usd(cfg, pos.ticker, int(md.m1_ts[pos.ticker][-1]))
            pnl = cfg.broker._close_market(pos, last_close, 'force_close') * _fx
            st.closed_trades.append({
                'ticker': pos.ticker, 'direction': pos.direction,
                'entry_price': pos.entry_price, 'exit_price': last_close,
                'shares': pos.shares, 'pnl': pnl, 'pnl_quote': pos.pnl,
                'reason': 'force_close',
                'entry_ts': str(pos.entry_time),
                'entry_i': int(pos._skip_ts or 0),
                'exit_i': int(md.m1_ts[ticker][-1]),
            })
            st.equity += pnl
    st.open_pos.clear()


def mtm_mdd(md: MarketData, cfg: Config, trades: list[dict], eq0: float | None = None) -> tuple[float, float, float]:
    """MTM MDD по M1 (как канон engine): worst=lo, best=hi."""
    if not trades:
        return 0.0, float(eq0 or cfg.eq_cap or 0), 0.0
    times = np.unique(np.concatenate([md.m1_ts[t] for t in md.m1_ts]))
    n = len(times)
    fl = np.zeros(n); fl_w = np.zeros(n); fl_b = np.zeros(n)

    def _contrib(seg_vals, seg_gidx, g0, g1):
        out = np.zeros(g1 - g0)
        loc = seg_gidx - g0
        out[loc] = seg_vals
        m = np.zeros(g1 - g0, dtype=bool); m[loc] = True
        idx = np.maximum.accumulate(np.where(m, np.arange(g1 - g0), -1))
        return np.where(idx >= 0, out[np.maximum(idx, 0)], 0.0)

    for tr in trades:
        tk = tr['ticker']
        spec = cfg.specs.get(tk, {'ms': 1.0, 'sp': 1.0})
        k = spec['sp'] * tr['shares'] / max(spec['ms'], 1e-9)
        # MTM тоже в USD: иначе для не-USD котировок просадка складывает разные валюты
        k *= _fx_usd(cfg, tk, tr.get('exit_i') or tr.get('entry_i') or 0)
        if tr['direction'] == 'short':
            k = -k
        i0, i1 = tr.get('entry_i'), tr.get('exit_i')
        if i0 is None or i1 is None:
            continue
        tsa = md.m1_ts[tk]
        a = int(np.searchsorted(tsa, i0, 'left'))
        b = int(np.searchsorted(tsa, i1, 'right'))
        if b <= a:
            continue
        gi = np.searchsorted(times, tsa[a:b], 'left')
        g0, g1 = int(gi[0]), int(gi[-1]) + 1
        ep = tr['entry_price']
        _ph = (md.m1_hi[tk][a:b] - ep) * k
        _pl = (md.m1_lo[tk][a:b] - ep) * k
        fl[g0:g1] += _contrib((md.m1_prc[tk][a:b] - ep) * k, gi, g0, g1)
        fl_b[g0:g1] += _contrib(np.maximum(_ph, _pl), gi, g0, g1)
        fl_w[g0:g1] += _contrib(np.minimum(_ph, _pl), gi, g0, g1)

    eq0 = float(eq0) if eq0 else (float(cfg.eq_cap) if cfg.eq_cap > 0 else 200_000.0)
    delta = np.zeros(n + 1)
    for tr in trades:
        e = tr.get('exit_i')
        if e is None:
            continue
        delta[int(np.searchsorted(times, e, 'right'))] += tr['pnl']
    real = eq0 + np.cumsum(delta[:-1])

    mtm_lo = real + fl_w
    peak = np.maximum.accumulate(real + fl_b)
    dd = np.where(peak > 0, (peak - mtm_lo) / peak, 0.0)
    j = int(np.argmax(dd))
    return float(dd[j]), float(peak[j]), float(mtm_lo[j])
