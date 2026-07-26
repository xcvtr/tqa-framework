"""PG state — connect, CRUD, ensure_tables.

PG_URL из env. Если не задан — localhost:5433/tqa (тестовый Docker PG).
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras


def _pg_url() -> str:
    """PG URL из окружения или тестовый локальный."""
    return os.environ.get(
        "PG_URL",
        "postgresql://postgres:***@localhost:5433/tqa",
    )


def _parse_url(url: str) -> dict:
    """Разобрать postgresql://user:pass@host:port/dbname."""
    rest = url.removeprefix("postgresql://")
    user_pass, rest = rest.split("@", 1)
    user, password = user_pass.split(":", 1)
    host_port, dbname = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 5432
    return dict(user=user, host=host, port=port, dbname=dbname, password=password)


class PGState:
    """PostgreSQL state management. Единое API для всех стратегий.

    PG_URL из env или параметра pg_url.
    Если не задан — localhost:5433/tqa (тестовый Docker PG).
    """

    def __init__(self, pg_url: str = ""):
        if pg_url:
            parts = _parse_url(pg_url)
        else:
            parts = _parse_url(_pg_url())
        self.host = parts["host"]
        self.port = parts["port"]
        self.dbname = parts["dbname"]
        self.user = parts["user"]
        self.password = parts.get("password", "")
        self._conn = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self.host, port=self.port,
                dbname=self.dbname, user=self.user,
                password=self.password if self.password and self.password != "***" else "tqa",
            )
            self._conn.autocommit = True
        return self._conn

    def ensure_schemas(self):
        """Создать схемы если нет."""
        schemas = ["futures", "backtest", "shared", "mt5", "fx_top"]
        with self.conn.cursor() as cur:
            for s in schemas:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")

    def ensure_tables_backtest(self):
        """Создать таблицы для бэктеста."""
        with self.conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS backtest")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS backtest.trades (
                    id SERIAL PRIMARY KEY,
                    strategy text,
                    ticker text,
                    direction text,
                    entry_time timestamptz,
                    exit_time timestamptz,
                    entry_price float,
                    exit_price float,
                    quantity float,
                    pnl float,
                    pnl_pct float,
                    exit_reason text,
                    tags jsonb DEFAULT '[]'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS backtest.equity_curve (
                    id SERIAL PRIMARY KEY,
                    strategy text,
                    bar_time timestamptz,
                    equity float,
                    cash_equity float DEFAULT 0,
                    drawdown float,
                    summary_id int REFERENCES backtest.summary(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS backtest.summary (
                    id SERIAL PRIMARY KEY,
                    strategy text,
                    tickers text[],
                    tf int,
                    days int,
                    start_equity float,
                    end_equity float,
                    total_return float,
                    mdd float,
                    win_rate float,
                    profit_factor float,
                    total_trades int,
                    calmar_ratio float,
                    params jsonb,
                    created_at timestamptz DEFAULT now()
                )
            """)

    # --- CRUD для trades ---

    def save_trade(self, trade: dict):
        """Сохранить закрытую сделку в backtest.trades."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO backtest.trades
                    (strategy, ticker, direction,
                     entry_time, exit_time,
                     entry_price, exit_price,
                     quantity, pnl, pnl_pct, exit_reason, tags)
                VALUES (%(strategy)s, %(ticker)s, %(direction)s,
                        %(entry_time)s, %(exit_time)s,
                        %(entry_price)s, %(exit_price)s,
                        %(quantity)s, %(pnl)s, %(pnl_pct)s, %(exit_reason)s,
                        %(tags)s::jsonb)
            """, trade)

    def save_trades_batch(self, trades: list[dict]):
        """Сохранить список сделок батчем."""
        if not trades:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO backtest.trades
                    (strategy, ticker, direction,
                     entry_time, exit_time,
                     entry_price, exit_price,
                     quantity, pnl, pnl_pct, exit_reason, tags)
                VALUES %s
                """,
                trades,
                template="(%(strategy)s, %(ticker)s, %(direction)s, "
                         "%(entry_time)s, %(exit_time)s, "
                         "%(entry_price)s, %(exit_price)s, "
                         "%(quantity)s, %(pnl)s, %(pnl_pct)s, "
                         "%(exit_reason)s, %(tags)s::jsonb)"
            )

    def save_equity_points(self, points: list[dict], summary_id: int = 0):
        """Сохранить точки equity кривой."""
        if not points:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO backtest.equity_curve
                    (strategy, bar_time, equity, cash_equity, drawdown, summary_id)
                VALUES %s
                """,
                [(p["strategy"], p["bar_time"], p["equity"],
                  p.get("cash_equity", 0), p["drawdown"], summary_id) for p in points],
            )

    def save_summary(self, summary: dict) -> int:
        """Сохранить итоговую сводку бэктеста.
        Returns:
            id вставленной записи
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO backtest.summary
                    (strategy, tickers, tf, days,
                     start_equity, end_equity, total_return,
                     mdd, win_rate, profit_factor,
                     total_trades, calmar_ratio, params)
                VALUES (%(strategy)s, %(tickers)s, %(tf)s, %(days)s,
                        %(start_equity)s, %(end_equity)s, %(total_return)s,
                        %(mdd)s, %(win_rate)s, %(profit_factor)s,
                        %(total_trades)s, %(calmar_ratio)s, %(params)s::jsonb)
                RETURNING id
            """, summary)
            return cur.fetchone()[0]

    def load_summaries(self, strategy: str, limit: int = 10) -> list[dict]:
        """Загрузить последние сводки бэктеста."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM backtest.summary "
                "WHERE strategy = %s ORDER BY created_at DESC LIMIT %s",
                (strategy, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    # --- CRUD для live стратегий ---

    def ensure_tables_live(self, schema: str):
        """Создать таблицы для live paper trader'а в указанной схеме."""
        with self.conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema}.pending_signals (
                    id SERIAL PRIMARY KEY,
                    symbol text, direction text,
                    price float, timestamp timestamptz,
                    strategy text,
                    score float DEFAULT 1.0,
                    processed boolean DEFAULT false,
                    created_at timestamptz DEFAULT now()
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema}.positions (
                    id SERIAL PRIMARY KEY,
                    symbol text, direction text,
                    entry_price float, quantity float,
                    sl_price float, tp_price float,
                    entry_time timestamptz,
                    trail_activation float,
                    trail_distance float,
                    strategy text
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema}.state (
                    key text PRIMARY KEY,
                    value jsonb,
                    updated_at timestamptz DEFAULT now()
                )
            """)

    def save_signal(self, schema: str, signal: dict):
        """Сохранить pending signal."""
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {schema}.pending_signals "
                f"(symbol, direction, price, timestamp, strategy, score) "
                f"VALUES (%s, %s, %s, %s, %s, %s)",
                (signal["symbol"], signal["direction"],
                 signal["price"], signal["timestamp"],
                 signal.get("strategy", ""), signal.get("score", 1.0)),
            )

    def load_pending_signals(self, schema: str) -> list[dict]:
        """Загрузить необработанные сигналы."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM {schema}.pending_signals "
                f"WHERE NOT processed ORDER BY created_at"
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_processed(self, schema: str, signal_id: int):
        """Пометить сигнал как обработанный."""
        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE {schema}.pending_signals "
                f"SET processed = true WHERE id = %s",
                (signal_id,),
            )
