"""Data provider tqa-f: M1 бары форекса из CH forex.bars.

Контракт времени (проект TQA-FX-TOP, доказан 2026-10-20): таблица forex.bars
неоднородна — история (до 2026-08-10) хранится как истинный UTC − 2 ч,
live (с 2026-08-10) — истинный UTC. Здесь приводим всё к истинному UTC,
дальше по проекту время везде UTC (стандарт tqa-f).

Формат бара — как у tqa-m: {ts, opn, hi, lo, prc, vol}, ts = naive UTC.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import clickhouse_connect as cc

log = logging.getLogger('tqa_f.data')

CH_HOST = '10.0.0.64'
CH_PORT = 8123
CH_DB = 'forex'
TABLE = os.environ.get('FOREX_BARS_TABLE', 'forex.bars')

# шов: до этой даты stored = true_utc − 2ч
SEAM = datetime(2026, 8, 10)


def ch_client(host=CH_HOST, port=CH_PORT, database=CH_DB):
    return cc.get_client(host=host, port=port, database=database)


def _to_true_utc(ts: datetime) -> datetime:
    """stored-эпоха -> истинный UTC (naive на выходе)."""
    naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
    return naive + timedelta(hours=2) if naive < SEAM else naive


def _is_session_tick(ts: datetime) -> bool:
    """Отсечь бары вне торговой недели (сб целиком, вс до 21:00, пт после 21:00 UTC)."""
    wd = ts.weekday()
    if wd == 5:
        return False
    if wd == 6 and ts.hour < 21:
        return False
    if wd == 4 and ts.hour >= 21:
        return False
    return True


def load_ticker_bars(ticker: str, start: str = '2024-01-01', end: str | None = None,
                     drop_flat: bool = False) -> list[dict]:
    """M1 бары тикера, истинный UTC, без дублей минуты.

    start/end — даты истинного UTC (YYYY-MM-DD). Окно запроса расширено на ±1 день,
    т.ч. сдвиг эпохи применяется после чтения.

    CH forex.bars stores symbols in lowercase (LowCardinality(String)),
    поэтому тикер приводится к нижнему регистру перед запросом.
    """

    # CH forex.bars stores symbols in lowercase (LowCardinality(String))
    ticker = ticker.lower()

    if end is None:
        end = datetime.utcnow().strftime('%Y-%m-%d')
    st_q = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    en_q = (datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    sql = f"""SELECT time, open, high, low, close, vol FROM {TABLE}
        WHERE symbol = '{ticker}'
          AND time >= toDateTime('{st_q} 00:00:00') AND time <= toDateTime('{en_q} 23:59:59')
        ORDER BY time"""
    try:
        client = ch_client()
        rows = client.query(sql).named_results()
        client.close()
    except Exception as e:  # сеть/CH
        log.error('load_ticker_bars %s: %s', ticker, e)
        return []

    st_dt = datetime.strptime(start, '%Y-%m-%d')
    en_dt = datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)
    out, seen = [], set()
    for r in rows:
        utc = _to_true_utc(r['time'])
        if utc < st_dt or utc >= en_dt or not _is_session_tick(utc):
            continue
        minute = utc.replace(second=0, microsecond=0)
        if minute in seen:
            continue
        o, h, lo, cl = float(r['open']), float(r['high']), float(r['low']), float(r['close'])
        if drop_flat and (o == 0 or h == 0 or lo == 0 or cl == 0):
            continue
        seen.add(minute)
        out.append({'ts': minute, 'opn': o, 'hi': h, 'lo': lo, 'prc': cl, 'vol': float(r['vol'] or 0)})
    return out


def load_multiple_tickers(tickers: list[str], start: str, end: str | None = None) -> dict[str, list[dict]]:
    result = {}
    for ticker in tickers:
        bars = load_ticker_bars(ticker, start, end)
        if bars:
            result[ticker] = bars
        else:
            log.warning('load_multiple_tickers: нет данных для %s', ticker)
    return result
