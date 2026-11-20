"""PG state — connect, CRUD, ensure_tables.

PG_URL из env. Если не задан — localhost:5433/tqa (тестовый Docker PG).
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

import logging

import psycopg2
import psycopg2.extras

log = logging.getLogger("tqa_m.state.pg_state")


def _pg_url() -> str:
    """PG URL из окружения или тестовый локальный."""
    return os.environ.get(
        "PG_URL",
        "postgresql://postgres:***@localhost:5433/tqa",
    )


def _parse_url(url: str) -> dict:
    """Разобрать postgresql://user:***@host:port/dbname."""
    rest = url.removeprefix("postgresql://")
    user_pass, rest = rest.split("@", 1)
    if ":" in user_pass:
        user, password = user_pass.split(":", 1)
    else:
        user, password = user_pass, ""
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
            # summary первой — trades/equity_curve ссылаются на неё
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
                    tags jsonb DEFAULT '[]',
                    summary_id int REFERENCES backtest.summary(id) ON DELETE CASCADE
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

    # --- CRUD для trades ---

    def save_trade(self, trade: dict):
        """Сохранить закрытую сделку в backtest.trades."""
        if isinstance(trade.get("tags"), dict):
            trade["tags"] = json.dumps(trade["tags"])
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

    def save_trades_batch(self, trades: list[dict], summary_id: int = 0):
        """Сохранить список сделок батчем."""
        if not trades:
            return
        # Auto-convert tags dict → JSON string
        for trade in trades:
            if isinstance(trade.get("tags"), dict):
                trade["tags"] = json.dumps(trade["tags"])
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO backtest.trades
                    (strategy, ticker, direction,
                     entry_time, exit_time,
                     entry_price, exit_price,
                     quantity, pnl, pnl_pct, exit_reason, tags,
                     summary_id)
                VALUES %s
                """,
                trades,
                template="(%(strategy)s, %(ticker)s, %(direction)s, "
                         "%(entry_time)s, %(exit_time)s, "
                         "%(entry_price)s, %(exit_price)s, "
                         "%(quantity)s, %(pnl)s, %(pnl_pct)s, "
                         "%(exit_reason)s, %(tags)s::jsonb, "
                         f"{summary_id})"
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

    # --- CRUD для strategies (shared.strategies) ---

    def ensure_tables_strategies(self):
        """Создать таблицу shared.strategies."""
        with self.conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS shared")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shared.strategies (
                    name TEXT PRIMARY KEY,
                    version TEXT NOT NULL DEFAULT '2.0',
                    yaml TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    def save_strategy(self, name: str, yaml_str: str, version: str = "2.0"):
        """Сохранить или обновить стратегию в PG."""
        self.ensure_tables_strategies()
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO shared.strategies (name, version, yaml, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    version = EXCLUDED.version,
                    yaml = EXCLUDED.yaml,
                    updated_at = NOW()
            """, (name, version, yaml_str))

    def load_strategy_yaml(self, name: str) -> str | None:
        """Загрузить YAML стратегии из PG по имени."""
        self.ensure_tables_strategies()
        with self.conn.cursor() as cur:
            cur.execute("SELECT yaml FROM shared.strategies WHERE name = %s", (name,))
            row = cur.fetchone()
            return row[0] if row else None

    def list_strategies(self) -> list[dict]:
        """Список всех стратегий в PG."""
        self.ensure_tables_strategies()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT name, version, created_at, updated_at FROM shared.strategies ORDER BY name")
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


# ─── Многопозиционный state-journal (перенесено из мёртвого tqa_m/executor) ───
# CH = сырьё, PG = состояние; бар в state не попадает.

def load_ticker_specs_pg() -> dict:
    """ticker → {min_step, step_price, fee_entry, go, pct}. Из PG futures.ticker_specs."""
    try:
        import psycopg2
        pg_url = os.environ.get('PG_URL',
            'postgresql://postgres@10.0.0.60:5432/moex')
        conn = psycopg2.connect(pg_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, go, min_step, step_price, COALESCE(fee_entry, 4.0) AS fee_entry, pct
            FROM futures.ticker_specs
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        specs = {}
        for row in rows:
            specs[row[0]] = {
                'go': float(row[1]) if row[1] is not None else 0,
                'min_step': float(row[2]) if row[2] is not None else 0.01,
                'step_price': float(row[3]) if row[3] is not None else 1.0,
                'fee_entry': float(row[4]),
                'pct': float(row[5]) if row[5] is not None else 1.0,
            }
        return specs
    except Exception as e:
        log.warning("PG ticker_specs error %s", e)
        return {}


# ─── PG State (schema own, parity with tqa-c) ────────────────────────────────

DEFAULT_INIT_EQ = 200_000.0   # руб., канон S2 eq_cap

DDL_STATE = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.multi_state (
    id INT PRIMARY KEY CHECK (id = 1),
    equity NUMERIC, peak NUMERIC, balance NUMERIC,
    positions JSONB DEFAULT '{{}}'::jsonb,
    last_ts BIGINT DEFAULT 0,     -- последний обработанный бар: рестарт не переигрывает день
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE {schema}.multi_state ADD COLUMN IF NOT EXISTS last_ts BIGINT DEFAULT 0;
"""

DDL_CLOSED = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.multi_closed_trades (
    id BIGSERIAL PRIMARY KEY,
    strategy TEXT NOT NULL DEFAULT 'trend_sma',
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,       -- LONG|SHORT
    pnl_rub NUMERIC,               -- PnL в рублях (gross - commission)
    ts_open TIMESTAMPTZ,
    ts_close TIMESTAMPTZ,
    entry_price NUMERIC,
    exit_price NUMERIC,
    reason TEXT,                   -- stop_loss | trailing_tp
    shares INTEGER,
    CONSTRAINT uq_closed_1 UNIQUE (symbol, direction, ts_open, entry_price)
);
"""

SEED_STATE = (
    "INSERT INTO {schema}.multi_state (id, equity, peak, balance, positions) "
    "VALUES (1, %s, %s, %s, '{{\"equity\": %s}}'::jsonb) "
    "ON CONFLICT (id) DO NOTHING"
)


class PaperDB:
    """PG-персист многопозиционной торговли: multi_state + multi_closed_trades.
    db=None → no persist (single-shot bt без записи)."""

    def __init__(self, conn, schema: str):
        self.conn = conn
        self.schema = schema
        self.closed_rows: list[dict] = []

    def ensure(self, init_eq: float):
        with self.conn.cursor() as cur:
            cur.execute(DDL_STATE.format(schema=self.schema))
            cur.execute(DDL_CLOSED.format(schema=self.schema))
            cur.execute(SEED_STATE.format(schema=self.schema),
                        (init_eq, init_eq, init_eq, init_eq))
        self.conn.commit()

    def reset(self):
        """Полная очистка state перед новым прогоном (dev/paper restart)."""
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.schema}.multi_closed_trades")
            cur.execute(f"DELETE FROM {self.schema}.multi_state")
        self.conn.commit()

    def load(self) -> dict | None:
        """Read multi_state → meta dict; None если fresh."""
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT positions FROM {self.schema}.multi_state WHERE id=1")
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            d = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return d if isinstance(d, dict) else {"equity": float(DEFAULT_INIT_EQ)}

    def load_state(self) -> dict | None:
        """Полный state из multi_state (equity/peak/balance/positions) или None."""
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT equity, peak, balance, positions, "
                        f"COALESCE(last_ts, 0) FROM {self.schema}.multi_state WHERE id=1")
            row = cur.fetchone()
        if not row:
            return None
        pos = row[3] if isinstance(row[3], dict) else (json.loads(row[3]) if row[3] else {})
        if isinstance(pos, dict) and 'equity' in pos and len(pos) == 1:
            pos = {}   # seed-заглушка SEED_STATE
        return {'equity': float(row[0]), 'peak': float(row[1]), 'balance': float(row[2]),
                'positions': pos or {}, 'last_ts': int(row[4] or 0)}

    def save(self, state: dict):
        """Persist positions/meta JSONB to multi_state."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self.schema}.multi_state SET "
                    f"equity=%s, peak=%s, balance=%s, positions=%s, last_ts=%s, updated_at=NOW()",
                    (state.get('equity'), state.get('peak'), state.get('balance'),
                     json.dumps(state.get('positions', {}), default=_json_default),
                     int(state.get('last_ts') or 0))
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            log.exception("PaperDB.save failed")
            raise

    def save_trade(self, trade: dict):
        """Append closed trade to multi_closed_trades."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(DDL_CLOSED.format(schema=self.schema))
                cur.execute(
                    f"INSERT INTO {self.schema}.multi_closed_trades "
                    f"(strategy,symbol,direction,pnl_rub,ts_open,ts_close,"
                    f"entry_price,exit_price,reason,shares) VALUES "
                    f"(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (trade['strategy'], trade['symbol'], trade['direction'],
                     trade['pnl_rub'], trade.get('ts_open'), trade.get('ts_close'),
                     trade['entry_price'], trade['exit_price'], trade['reason'],
                     trade.get('shares', 0))
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            log.exception("PaperDB.save_trade failed")
            raise


def _as_ns(dt) -> Optional[int]:
    """datetime → numpy.datetime64[ns]."""
    import numpy as np
    if dt is None:
        return None
    if not hasattr(dt, 'year'):
        try:
            dt = datetime.fromtimestamp(dt, tz=timezone.utc)
        except Exception:
            return None
    return np.datetime64(dt, 'ns')


def _iso(ns):
    """numpy.datetime64[ns] → ISO string."""
    if ns is None:
        return None
    s = str(ns).replace('+00:00', '')
    if len(s) > 19:
        s = s[:19]
    return s + '+03:00'  # MSK


def _json_default(obj):
    """JSON serialisation helper for numpy types."""
    import numpy as np
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
