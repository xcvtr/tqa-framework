"""Источник сигналов из PG: то, что пишет контейнер детектора.

Движок не вызывает detect — он спрашивает `get(ticker, date)`.
Ключ идемпотентности — (symbol, дата решения), поэтому повторный день при реплее
не плодит дубли и `processed` не ставим.
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras

from strategies.trend_sma.engine import SignalView


class PGSignalSource:
    def __init__(self, schema: str, strategy: str = 'trend_sma'):
        self.schema = schema
        self.strategy = strategy
        self._cache: dict[tuple[str, str], list[SignalView]] = {}
        self.conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')

    def get(self, ticker: str, date: str) -> list[SignalView]:
        key = (ticker, date)
        if key in self._cache:
            return self._cache[key]
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT direction, price FROM {self.schema}.pending_signals "
                f"WHERE symbol = %s AND strategy = %s AND timestamp::date = %s::date "
                f"ORDER BY id", (ticker, self.strategy, date))
            rows = cur.fetchall()
        out = [SignalView(r['direction'], r['price']) for r in rows]
        self._cache[key] = out
        return out
