"""Data provider для tqa-m MOEX: загрузка M1 bars из CH mt5_roll.

Соответствует load_bars() из TQA-MOEX-futures-framework/strategies/common/backtest.py
для source='roll'. Читаем напрямую ClickHouse, возвращаем list[dict] как в каноне.

Формат бара: {ts, opn, hi, lo, prc, vol} — как в old backtest.py строка 289-290.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta as _td

import clickhouse_connect as cc

log = logging.getLogger("tqa_m.data")


def ch_client(host='10.0.0.60', port=8123, database='moex'):
    """ClickHouse connect."""
    return cc.get_client(host=host, port=port, database=database)


def load_ticker_bars(
    ticker: str,
    start: str = '2024-08-01',
    end: str | None = None,
    keep_dup_min: bool = False,
) -> list[dict]:
    """Загрузить M1 бары тикера из CH moex.mt5_roll (как canonical bt).
    
    Args:
        ticker: символ (CM, AL, SV, ...)
        start: YYYY-MM-DD начала прогона
        end: YYYY-MM-DD конца прогона (None = сегодня)
        keep_dup_min: True = не дедуп минуты (старое поведение); False = берём последнюю строку минуты
    
    Returns:
        [{ts, opn, hi, lo, prc, vol}, ...] — sorted по ts ascending
    """
    tbl = 'moex.mt5_roll'
    if end is None:
        end = datetime.now(timezone(_td(hours=8))).strftime('%Y-%m-%d')
    
    where = f"WHERE ticker = '{ticker}' AND bt >= toDateTime('{start} 00:00:00') AND bt <= toDateTime('{end} 23:59:59')"
    
    if keep_dup_min:
        sql = f"SELECT bt, opn, hi, lo, prc, vol FROM {tbl} {where} ORDER BY bt"
    else:
        sql = f"""
            SELECT bt, opn, hi, lo, prc, vol FROM (
                SELECT bt, opn, hi, lo, prc, vol FROM {tbl} {where}
                ORDER BY bt DESC LIMIT 1 BY toStartOfMinute(bt)
            ) ORDER BY bt
        """
    
    try:
        client = ch_client()
        result = client.query(sql)
        rows = result.named_results()
        client.close()
    except Exception as e:
        log.error("load_ticker_bars %s error: %s", ticker, e)
        return []
    
    # Все бары mt5_roll как в каноне (old backtest.py source='roll'):
    # БЕЗ фильтрации часов — канон входит по ПЕРВОМУ бару дня по МСК (может быть 07:05, 09:05, ...).
    # Раньше этот фильтр вырезал 07-09 МСК → первый бар дня всегда 10:00 → другие цены/дни входа.
    return [
        {
            'ts': row['bt'],
            'opn': float(row['opn']),
            'hi': float(row['hi']),
            'lo': float(row['lo']),
            'prc': float(row['prc']),
            'vol': float(row.get('vol', 0)),
        }
        for row in rows
    ]


def load_multiple_tickers(tickers: list[str], start: str, end: str | None = None) -> dict[str, list[dict]]:
    """Загрузить несколько тикеров. Возвращает {ticker: [bars]}."""
    result = {}
    for ticker in tickers:
        bars = load_ticker_bars(ticker, start, end)
        if bars:
            result[ticker] = bars
        else:
            log.warning("load_multiple_tickers: нет данных для %s", ticker)
    return result
