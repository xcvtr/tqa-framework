"""trend_sma — режимный канон (MOEX futures), контракт tqa_framework.

Режим по дневным SMA50/100 (по close):
    close(d) > SMA50(d) > SMA100(d)  -> LONG
    close(d) < SMA50(d) < SMA100(d)  -> SHORT
    иначе                             -> FLAT (нет входа)
Вход: ПЕРВЫЙ бар дня d+1 по close, направление = режим дня d.
Пере-вход: детект выдаёт сигнал на первом баре КАЖДОГО дня, пока режим
предыдущего дня держится (L/S) -> после любого выхода executor снова
откроет позу на следующем дне. Это и даёт n сделок канона (2246 vs 335
у репликанта с одним входом на эпоху тренда).

Anti-look-ahead:
  - Режим дня d известен по закрытию d -> вход на первом баре d+1 чист.
  - SMA считаются только по закрытиям ПРОШЛЫХ дней (не текущий день).
  - Сигнал выдаётся только на первом баре дня (текущий формирующийся).

bars: бары с ключами ts, open, high, low, close (M1 или 5м).
TZ: дата дня берётся по МСК (UTC+3) — как в oi_slot5.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tqa_f.connector.exchange_base import Signal

DEFAULT_PARAMS = {
    'sma_fast': 50,
    'sma_slow': 100,
    'direction': 'AUTO',   # направление = режим дня (L/S)
    'ma_type': 'sma',      # 'sma' | 'ema' — тип дневной скользящей для режима
    # Единое live-окно с папером: источник + предыстория детекта. Папер и канон
    # обязаны читать ОДИН `window_days`/`source` из PG params (не CLI-дефолты).
    'source': 'roll',
    'window_days': 150,
}


def _msk_dt(ts):
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(ts).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _utc_date(ts) -> str | None:
    dt = _msk_dt(ts)
    return dt.date().isoformat() if dt is not None else None


def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _ema(values, n):
    """EMA (1-price seed). Корректно для длинных рядов дневных close."""
    if len(values) < n:
        return None
    k = 2.0 / (n + 1)
    seed = sum(values[:n]) / n
    e = seed
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


_MA = {'sma': _sma, 'ema': _ema}


# detect зовётся раз/день, а окно баров растёт (~150 торговых дней). Полный
# пересчёт _utc_date на каждый бар каждого вызова = O(N²) суммарно (главный
# хот-спот). bars_raw передаётся из engine повторно, ts — тот же Timestamp
# объект; кэш даты по значению ts превращает timezone-конвертацию в лукап.
_DATE_CACHE = {}
_DATE_CACHE_LIMIT = 1 << 18      # ~262k баров достаточно для 150 дней окна
_DATE_CACHE_KEYS: list = []


def _cached_utc_date(ts):
    d = _DATE_CACHE.get(ts)
    if d is not None:
        return d
    d = _utc_date(ts)
    if d is not None:
        _DATE_CACHE[ts] = d
        _DATE_CACHE_KEYS.append(ts)
        if len(_DATE_CACHE_KEYS) > _DATE_CACHE_LIMIT:
            drop = _DATE_CACHE_KEYS[:len(_DATE_CACHE_KEYS) // 2]
            for k in drop:
                _DATE_CACHE.pop(k, None)
            del _DATE_CACHE_KEYS[:len(_DATE_CACHE_KEYS) // 2]
    return d


def detect(bars: list[dict], config: dict) -> list[Signal]:
    """Режим SMA50/100 -> Signal на первом баре дня d+1 (направление = режим d)."""
    import os
    _dbg = os.environ.get('TREND_DEBUG')
    if not bars:
        return []
    params = {**DEFAULT_PARAMS, **config}
    symbol = config.get('symbol', '')
    fast = int(params.get('sma_fast', DEFAULT_PARAMS['sma_fast']))
    slow = int(params.get('sma_slow', DEFAULT_PARAMS['sma_slow']))

    days: dict[str, list[dict]] = {}
    for b in bars:
        d = _cached_utc_date(b.get('ts'))
        if d is None:
            continue
        days.setdefault(d, []).append(b)
    if os.environ.get('TREND_DEBUG'):
        print(f"TREND_DBG_EVERY {symbol} nbars={len(bars)} ndays={len(days)} "
              f"first={_cached_utc_date(bars[0].get('ts')) if bars else '?'} "
              f"cur={len(days[max(days)]) if days else 0} "
              f"slow={'ok' if len(days) >= slow else f'no({len(days)})'}", flush=True)
    if not days:
        return []

    cur_day = max(days)
    # Вход только на ПЕРВОМ баре дня: в окне дня ровно один бар (текущий формирующийся).
    # Если в окне пришло несколько баров одного дня — это не первый бар, пропускаем.
    if len(days[cur_day]) != 1:
        return []

    # Дневные close по завершённым дням (до текущего)
    prev_days = sorted(dd for dd in days if dd < cur_day)
    if not prev_days:
        return []
    closes = [float(days[dd][-1]['close']) for dd in prev_days]

    # Режим дня d = последний завершённый день (prev_days[-1])
    if len(closes) < slow:
        return []
    prev_day = prev_days[-1]
    c = closes[-1]
    ma = _MA.get(str(params.get('ma_type', 'sma')).lower(), _sma)
    s_f = ma(closes, fast)
    s_s = ma(closes, slow)
    if s_f is None or s_s is None:
        return []
    if c > s_f > s_s:
        direction = 'LONG'
    elif c < s_f < s_s:
        direction = 'SHORT'
    else:
        direction = None

    if _dbg:
        print(f"TREND_DBG {symbol} cur={cur_day} prev={prev_day} c={c:.1f} "
              f"sma{fast}={s_f:.1f} sma{slow}={s_s:.1f} dir={direction} ncl={len(closes)}",
              file=__import__('sys').stderr)

    if direction is None:
        return []

    bar = days[cur_day][0]
    price = float(bar['close'])
    ts = bar.get('ts')
    return [Signal(
        symbol=symbol,
        direction=direction,
        price=price,
        timestamp=str(ts),
        strategy='trend_sma',
        score=1.0,
        reason=f'trend_sma_{direction.lower()}_c{c:.0f}_f{s_f:.0f}_s{s_s:.0f}',
    )]
