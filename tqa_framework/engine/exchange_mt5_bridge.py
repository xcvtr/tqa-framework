"""Live MT5 bridge executor (forex/CFD) — реализация TODO-заглушки.

Единственный executor-файл, разрешённый к реализации (спека fx7_live_spec.md,
раздел D «Этап 1/2 — Executor»). Остальные ``exchange_*.py`` — IMMUTABLE, не трогаем.
Новый сценарий = новый класс, не менять этот файл после реализации.

Контур (1:1 с прод TQA-FX-TOP ``mt5_bridge.py`` / ``paper_trader.py``):

    стратегия → ExchangeMT5.open_position()
        → INSERT fx_top.mt5_signals (type='ENTER', action=BUY/SELL)
        → MT5BridgeClient.execute_signal()   # реальный Wine-терминал MetaTrader5
             или «заглушка-через-PG» (терминал недоступен → сигнал остаётся в PG,
             его подхватит отдельный Wine-bridge)
    get_positions()/get_account_balance() читают fx_top.mt5_account
    (equity/positions), которые пишет bridge.

Все метки времени — UTC. Реальных сделок эта реализация не совершает до тех пор,
пока не подключён живой MT5-терминал: в тестах bridge подменяется mock'ом.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

import psycopg2
import psycopg2.extras

from tqa_framework.engine.exchange_base import (
    ExchangeBase, ExchangeConfig, Position, Signal,
)

# ── MT5 symbol mapping (AlfaForexRU-Real использует суффикс *rfd) ────────
MT5_SYMBOL_MAP = {
    'AUDJPY': 'AUDJPYrfd', 'AUDUSD': 'AUDUSDrfd', 'EURAUD': 'EURAUDrfd',
    'EURGBP': 'EURGBPrfd', 'EURJPY': 'EURJPYrfd', 'EURUSD': 'EURUSDrfd',
    'GBPJPY': 'GBPJPYrfd', 'GBPUSD': 'GBPUSDrfd', 'NZDUSD': 'NZDUSDrfd',
    'USDCAD': 'USDCADrfd', 'USDCHF': 'USDCHFrfd', 'USDJPY': 'USDJPYrfd',
    'XAUUSD': 'XAUUSDrfd',
}
MT5_SYM_REVERSE = {v: k for k, v in MT5_SYMBOL_MAP.items()}

BASE_LOT = 0.01
MAX_POSITIONS = 10
MAGIC = 202606

# ── Инфра PG по умолчанию: прод fx_top (env-переопределение) ────────────
DEFAULT_PG = dict(
    host=os.environ.get("FOREX_PG_HOST", "10.0.0.60"),
    port=int(os.environ.get("FOREX_PG_PORT", "5432")),
    dbname=os.environ.get("FOREX_PG_NAME", "forex"),
    schema=os.environ.get("FOREX_PG_SCHEMA", "fx_top"),
    user=os.environ.get("FOREX_PG_USER", "postgres"),
    password=os.environ.get("FOREX_PG_PASS", ""),
)


class BridgeUnavailable(RuntimeError):
    """Реальный MT5-терминал (Wine) недоступен — сигнал остаётся в PG."""


def mt5_sym(symbol: str, suffix: str = "rfd") -> str:
    """Наш символ (EURUSD) → MT5 (EURUSDrfd). Case-insensitive."""
    upper = symbol.upper()
    mapped = MT5_SYMBOL_MAP.get(upper)
    if mapped:
        return mapped
    return upper + suffix if suffix else upper


def reverse_sym(mt5_symbol: str) -> str:
    """MT5 (EURUSDrfd) → наш (EURUSD)."""
    return MT5_SYM_REVERSE.get(mt5_symbol, mt5_symbol.replace("rfd", "").upper())


def next_signal_id() -> str:
    """Уникальный id сигнала (UTC)."""
    now = datetime.now(timezone.utc)
    return f"fx7_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond // 1000:03d}"


# ══════════════════════════════════════════════════════════════════════
# ── SQL-хелперы fx_top (DDL из прод mt5_bridge.ensure_schema) ──────────
def ensure_mt5_schema(conn, schema: str) -> None:
    """Создать таблицы mt5_signals/mt5_account/mt5_loss_tracker/mt5_trade_log."""
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.mt5_signals (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'ENTER',
            action TEXT NOT NULL,
            symbol TEXT NOT NULL,
            entry NUMERIC,
            sl_pips NUMERIC DEFAULT 30,
            tp_pips NUMERIC DEFAULT 60,
            lot_size NUMERIC DEFAULT {BASE_LOT},
            created_at TIMESTAMPTZ DEFAULT NOW(),
            processed_at TIMESTAMPTZ
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.mt5_account (
            id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            balance NUMERIC DEFAULT 0,
            equity NUMERIC DEFAULT 0,
            margin NUMERIC DEFAULT 0,
            profit NUMERIC DEFAULT 0,
            margin_level NUMERIC,
            server TEXT,
            login BIGINT,
            currency TEXT,
            positions_count INT DEFAULT 0,
            positions JSONB DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.mt5_loss_tracker (
            symbol TEXT PRIMARY KEY,
            consecutive_losses INT DEFAULT 0,
            skip_remaining INT DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.mt5_trade_log (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ DEFAULT NOW(),
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            volume NUMERIC,
            price NUMERIC,
            pnl NUMERIC,
            comment TEXT
        )
    """)
    cur.execute(f"INSERT INTO {schema}.mt5_account (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    conn.commit()


def insert_signal(conn, schema: str, row: dict) -> None:
    """INSERT сигнала в fx_top.mt5_signals (id, type, action, symbol, entry, sl, tp, lot)."""
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {schema}.mt5_signals
                (id, type, action, symbol, entry, sl_pips, tp_pips, lot_size)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (row["id"], row.get("type", "ENTER"), row["action"], row["symbol"],
              row.get("entry"), row.get("sl_pips", 30), row.get("tp_pips", 60),
              row.get("lot_size", BASE_LOT)))
    conn.commit()


def mark_processed(conn, schema: str, sig_id: str, actual_lot: Optional[float] = None) -> None:
    """Отметить сигнал исполненным (processed_at)."""
    with conn.cursor() as cur:
        if actual_lot:
            cur.execute(f"UPDATE {schema}.mt5_signals SET processed_at=NOW(), lot_size=%s WHERE id=%s",
                        (actual_lot, sig_id))
        else:
            cur.execute(f"UPDATE {schema}.mt5_signals SET processed_at=NOW() WHERE id=%s", (sig_id,))
    conn.commit()


def log_trade(conn, schema: str, symbol: str, action: str, volume: float,
              price: float, comment: str) -> None:
    """Записать сделку в mt5_trade_log."""
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {schema}.mt5_trade_log (symbol, action, volume, price, comment)
            VALUES (%s, %s, %s, %s, %s)
        """, (symbol, action, volume, price, comment))
    conn.commit()


def save_account_status(conn, schema: str, account: dict, positions: list[dict]) -> None:
    """Обновить fx_top.mt5_account (equity/balance/positions) как в прод."""
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE {schema}.mt5_account SET
                balance=%s, equity=%s, margin=%s, profit=%s, margin_level=%s,
                server=%s, login=%s, currency=%s, positions_count=%s,
                positions=%s, updated_at=NOW()
            WHERE id=1
        """, (
            round(account.get("balance", 0), 2),
            round(account.get("equity", 0), 2),
            round(account.get("margin", 0), 2),
            round(account.get("profit", 0), 2),
            round(account["margin_level"], 2) if account.get("margin_level") else 0,
            account.get("server", ""),
            account.get("login", 0),
            account.get("currency", ""),
            len(positions),
            json.dumps(positions),
        ))
    conn.commit()


def load_account(conn, schema: str) -> Optional[dict]:
    """Прочитать строку fx_top.mt5_account (id=1) или None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {schema}.mt5_account WHERE id=1")
        row = cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["positions"] = out.get("positions") if isinstance(out.get("positions"), list) else []
    return out


# ══════════════════════════════════════════════════════════════════════
# ── MT5-терминал (реальный bridge; Wine-only, ленивый импорт MetaTrader5) ──
def _mt5_close_position(mt5, position, magic: int = MAGIC):
    """Закрыть одну позицию MT5. Возвращает (ok, msg)."""
    symbol = position.symbol
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, f"Cannot get tick for {symbol}"
    if position.type == 0:  # BUY → закрываем SELL
        order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
    else:  # SELL → закрываем BUY
        order_type, price = mt5.ORDER_TYPE_BUY, tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
        "type": order_type,
        "position": position.ticket,
        "price": price,
        "deviation": 10,
        "magic": magic,
        "comment": f"TQA-CLOSE {position.ticket}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result is None:
        return False, f"Close failed: no response (last_error={mt5.last_error()})"
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"Close failed: retcode={result.retcode}, {result.comment}"
    return True, f"Closed {symbol} pos #{position.ticket} @ {price}"


class MT5BridgeClient:
    """Тонкая обёртка над реальным MetaTrader5 (Wine-терминал).

    Порт прод ``mt5_bridge.py``: execute_signal, close_symbol, get_price,
    get_positions, get_account. В тестах подменяется mock-объектом — терминал
    не требуется (``BridgeUnavailable`` при отсутствии соединения).
    """

    def __init__(self, pg_params: dict, mt5_params: Optional[dict] = None):
        self.pg = pg_params
        self.mt5_params = mt5_params or {}
        self._mod = None

    # ── подключение ──
    def _mt5(self):
        if self._mod is not None:
            return self._mod
        import MetaTrader5 as _mt5  # Wine-only
        mp = self.mt5_params
        ok = _mt5.initialize()
        if not ok and mp.get("terminal_path"):
            ok = _mt5.initialize(path=mp["terminal_path"])
        if not ok and mp.get("login"):
            ok = _mt5.initialize(
                login=int(mp["login"]),
                server=mp.get("server", "AlfaForexRU-Real"),
                password=mp.get("password", ""),
            )
        if not ok:
            raise BridgeUnavailable(f"MT5 initialize failed: {_mt5.last_error()}")
        self._mod = _mt5
        return self._mod

    def _suffix(self) -> str:
        return self.mt5_params.get("symbol_suffix", "rfd")

    # ── market data ──
    def get_price(self, symbol: str) -> float:
        mt5 = self._mt5()
        name = mt5_sym(symbol, self._suffix())
        tick = mt5.symbol_info_tick(name)
        if tick is None:
            return 0.0
        return float(tick.bid or tick.ask or 0.0)

    def get_positions(self) -> list[dict]:
        mt5 = self._mt5()
        raw = mt5.positions_get() or []
        return [
            {"ticket": p.ticket, "symbol": p.symbol,
             "type": "buy" if p.type == 0 else "sell",
             "volume": p.volume, "price_open": p.price_open,
             "price_current": p.price_current, "profit": round(p.profit, 2),
             "swap": round(p.swap, 2)}
            for p in raw
        ]

    def get_account(self) -> dict:
        mt5 = self._mt5()
        a = mt5.account_info()
        if a is None:
            return {}
        return dict(
            balance=float(a.balance or 0), equity=float(a.equity or 0),
            margin=float(a.margin or 0), profit=float(a.profit or 0),
            margin_level=float(a.margin_level or 0),
            server=getattr(a, "server", ""), login=getattr(a, "login", 0),
            currency=getattr(a, "currency", ""),
        )

    # ── исполнение ──
    def close_symbol(self, symbol: str) -> tuple:
        """Закрыть все позиции по символу. Возвращает (ok, msg)."""
        mt5 = self._mt5()
        magic = int(self.mt5_params.get("magic", MAGIC))
        name = mt5_sym(symbol, self._suffix())
        existing = mt5.positions_get(symbol=name) or []
        if not existing:
            return True, f"No positions to close for {name}"
        closed = 0
        for p in existing:
            ok, _msg = _mt5_close_position(mt5, p, magic)
            if ok:
                closed += 1
        if closed:
            return True, f"Closed {closed}/{len(existing)} position(s) for {name}"
        return False, f"Failed to close positions for {name}"

    def execute_signal(self, sig: dict) -> tuple:
        """Исполнить сигнал на MT5 (порт прод mt5_bridge.execute_signal).

        Возвращает (ok, msg): ok=True значит «сигнал обработан» (можно помечать
        processed); ok=False — ордер отклонён/ошибка.
        """
        mt5 = self._mt5()
        magic = int(self.mt5_params.get("magic", MAGIC))
        sig_id = sig["id"]
        action = str(sig["action"]).upper()
        symbol_name = mt5_sym(sig["symbol"], self._suffix())
        sig_type = str(sig.get("type", "")).upper()

        # Stale check: ENTER старше 60 мин не исполняем (цена ушла)
        if sig_type == "ENTER":
            created = sig.get("created_at")
            if created:
                try:
                    created_dt = created if isinstance(created, datetime) \
                        else datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_min = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
                    if age_min > 60:
                        return True, f"Stale signal skipped ({age_min:.0f}m)"
                except Exception:
                    pass

        # EXIT: закрыть всё по символу
        if sig_type == "EXIT" or action == "EXIT":
            return self.close_symbol(sig.get("symbol", ""))

        # PYR (пирамидинг): нельзя без базовой позиции того же направления
        if sig_type == "PYR":
            existing = mt5.positions_get(symbol=symbol_name) or []
            if not existing:
                return True, f"PYR skipped (no base position for {symbol_name})"
            want_buy = action == "BUY"
            if not [p for p in existing if (p.type == 0) == want_buy]:
                return True, f"PYR skipped (no {action} position for {symbol_name})"

        entry_price = float(sig.get("entry", 0) or 0)
        lot = round(float(sig.get("lot_size", BASE_LOT)) / 0.01) * 0.01

        # 0. Ограничение лота по свободной марже (реальный расчёт MT5)
        account = mt5.account_info()
        if account and account.margin_free > 0:
            order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(symbol_name)
            price = tick.ask if action == "BUY" else tick.bid if tick else 0
            if price and lot > 0.01:
                margin_needed = mt5.order_calc_margin(order_type, symbol_name, lot, price)
                if margin_needed and margin_needed > account.margin_free * 0.7:
                    scale = account.margin_free * 0.7 / margin_needed
                    lot = max(0.01, int(lot * scale / 0.01) * 0.01)
                    if lot < 0.01:
                        return False, f"Insufficient margin (need {margin_needed:.0f}, free {account.margin_free:.0f})"

        # 1. Проверка символа
        symbol_info = mt5.symbol_info(symbol_name)
        if symbol_info is None:
            if not mt5.symbol_select(symbol_name, True):
                return False, f"Symbol {symbol_name} not found in MT5"
            symbol_info = mt5.symbol_info(symbol_name)
            if symbol_info is None:
                return False, f"Symbol {symbol_name} unavailable"

        # 2. Уже в позиции / реверс
        existing = mt5.positions_get(symbol=symbol_name) or []
        if existing:
            want_buy = action == "BUY"
            if [p for p in existing if (p.type == 0) == want_buy]:
                return True, f"Already in position: {action} {symbol_name}"
            for p in existing:
                ok, msg = _mt5_close_position(mt5, p, magic)
                if not ok:
                    return False, f"Cannot close opposing position: {msg}"

        # 3. Лимит числа позиций
        if len(mt5.positions_get() or []) >= MAX_POSITIONS:
            return False, f"Max positions ({MAX_POSITIONS}) reached"

        # 4. Исполнение
        tick = mt5.symbol_info_tick(symbol_name)
        if tick is None:
            return False, f"Cannot get tick for {symbol_name}"
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if action == "BUY" else tick.bid
        point = symbol_info.point
        normalise = (lambda x: round(x / point) * point) if point else (lambda x: x)

        # SL — per-symbol pct (как sizing в прод SYM_CONFIG)
        SL_PCT = {'audjpy': 2.0, 'euraud': 2.0, 'eurgbp': 2.0, 'eurjpy': 2.0, 'eurusd': 1.0,
                  'gbpjpy': 1.0, 'gbpusd': 2.0, 'usdchf': 2.0, 'xauusd': 3.0}
        pct = SL_PCT.get(str(sig.get("symbol", "")).lower(), 3.0)
        sl_raw = entry_price * (1 - pct / 100) if action == "BUY" else entry_price * (1 + pct / 100)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol_name,
            "volume": lot,
            "type": order_type,
            "price": normalise(price),
            "deviation": 10,
            "magic": magic,
            "comment": f"TQA-{sig_id[:12]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
            "sl": normalise(sl_raw),
        }
        result = mt5.order_send(request)
        if result is None:
            return False, f"Order failed: no response (last_error={mt5.last_error()})"
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"Order failed: retcode={result.retcode}, {result.comment}"
        return True, f"Opened {action} {symbol_name} lot={lot} @ {normalise(price)}"


# ══════════════════════════════════════════════════════════════════════
# ── ExchangeMT5 — executor для live-форекс контура ─────────────────────
class ExchangeMT5(ExchangeBase):
    """Live-форекс executor: fx_top.mt5_signals + MT5-терминал (bridge).

    Параметры ``config.params``:
      pg: {host, port, dbname, schema, user, password}   # инфра fx_top
      mt5: {login, server, password, terminal_path, magic, symbol_suffix}
      symbol_suffix: символьный суффикс MT5 (default 'rfd')
      sl_pips / tp_pips: дефолты сигнала ENTER (30/60)

    Тестовая подмена: передать ``bridge=MockBridge`` и/или ``pg_factory=...``.
    """

    def __init__(self, config: ExchangeConfig, bridge=None, pg_factory: Optional[Callable] = None):
        super().__init__(config)
        params = config.params or {}
        pg = dict(DEFAULT_PG)
        pg.update(params.get("pg") or {})
        self._pg = pg
        self._schema = pg.get("schema") or "fx_top"
        self._mt5_params = dict(params.get("mt5") or {})
        if "symbol_suffix" in params:
            self._mt5_params.setdefault("symbol_suffix", params["symbol_suffix"])
        self._sl_pips = float(params.get("sl_pips", 30))
        self._tp_pips = float(params.get("tp_pips", 60))
        self._pg_factory = pg_factory or self._connect
        self.bridge = bridge if bridge is not None else MT5BridgeClient(self._pg, self._mt5_params)

    # ── PG ──
    def _connect(self):
        return psycopg2.connect(
            host=self._pg["host"], port=int(self._pg.get("port", 5432)),
            dbname=self._pg["dbname"], user=self._pg["user"],
            password=self._pg.get("password", ""), connect_timeout=5,
        )

    def ensure_schema(self) -> None:
        """Создать fx_top-таблицы, если их нет."""
        with self._pg_factory() as conn:
            ensure_mt5_schema(conn, self._schema)

    # ── ABC: get_price ──
    def get_price(self, symbol: str) -> float:
        try:
            return float(self.bridge.get_price(symbol))
        except (BridgeUnavailable, AttributeError):
            pass
        return self._price_from_ch(symbol)

    def _price_from_ch(self, symbol: str) -> float:
        """Fallback: последний close forex.bars из ClickHouse (когда bridge нет)."""
        ch = self._mt5_params.get("ch_host") or self._pg.get("ch_host") or "10.0.0.60"
        port = self._mt5_params.get("ch_port") or self._pg.get("ch_port") or 8123
        sql = (f"SELECT close FROM forex.bars WHERE symbol='{symbol.lower()}' "
               f"ORDER BY time DESC LIMIT 1")
        url = f"http://{ch}:{port}/?query=" + urllib.parse.quote(sql)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = resp.read().decode().strip()
            if not body:
                return 0.0
            return float(body.splitlines()[-1])
        except Exception:
            return 0.0

    # ── ABC: get_positions ──
    def get_positions(self) -> list[Position]:
        rows = self._account_positions()
        if rows:
            return [self._row_to_position(r) for r in rows]
        # fallback: живой bridge (если терминал доступен)
        try:
            return [self._row_to_position(r) for r in self.bridge.get_positions()]
        except (BridgeUnavailable, AttributeError):
            return []

    def _account_positions(self) -> list[dict]:
        with self._pg_factory() as conn:
            row = load_account(conn, self._schema)
        if not row or not row.get("positions"):
            return []
        return row["positions"]

    # ── ABC: open_position ──
    def open_position(self, signal: Signal, quantity: float) -> Optional[Position]:
        if signal is None or not signal.symbol:
            return None
        action = "BUY" if signal.direction.upper() == "LONG" else "SELL"
        lot = max(BASE_LOT, round(float(quantity) / 0.01) * 0.01)
        sig_id = next_signal_id()
        row = dict(
            id=sig_id, type="ENTER", action=action, symbol=signal.symbol.upper(),
            entry=float(signal.price), sl_pips=self._sl_pips, tp_pips=self._tp_pips,
            lot_size=lot,
        )
        # 1) ENTER-сигнал в PG (bridge читает ту же таблицу)
        with self._pg_factory() as conn:
            insert_signal(conn, self._schema, row)

        # 2) реальный bridge.execute_signal или «заглушка-через-PG»
        stub = False
        try:
            ok, _msg = self.bridge.execute_signal(dict(row))
        except BridgeUnavailable:
            ok, stub = False, True
        if ok:
            with self._pg_factory() as conn:
                mark_processed(conn, self._schema, sig_id, lot)
            fill = self._fill_price(action, signal.symbol, signal.price)
        elif stub:
            # сигнал ждёт Wine-bridge; позиция paper-filled
            fill = signal.price
        else:
            return None  # ордер отклонён
        return Position(
            symbol=signal.symbol.upper(), direction=signal.direction.upper(),
            entry_price=fill, current_price=fill, quantity=lot,
            entry_time=signal.timestamp, id=sig_id,
        )

    def _fill_price(self, action: str, symbol: str, fallback: float) -> float:
        try:
            px = float(self.bridge.get_price(symbol))
            return px if px else fallback
        except (BridgeUnavailable, AttributeError):
            return fallback

    # ── ABC: close_position ──
    def close_position(self, position: Position) -> bool:
        if position is None or not position.symbol:
            return False
        sig_id = next_signal_id()
        # EXIT-сигнал в PG
        with self._pg_factory() as conn:
            insert_signal(conn, self._schema, dict(
                id=sig_id, type="EXIT", action="EXIT", symbol=position.symbol.upper(),
                entry=position.entry_price, sl_pips=0, tp_pips=0,
                lot_size=position.quantity or BASE_LOT,
            ))
        # закрыть на bridge (реальный или заглушка-через-PG)
        try:
            ok, _msg = self.bridge.close_symbol(position.symbol)
            return bool(ok)
        except BridgeUnavailable:
            return True  # EXIT-сигнал остаётся в PG для Wine-bridge

    # ── ABC: get_account_balance ──
    def get_account_balance(self) -> float:
        with self._pg_factory() as conn:
            row = load_account(conn, self._schema)
        if not row:
            return 0.0
        return float(row.get("equity", 0) or 0)

    # ── mapping ──
    def _row_to_position(self, r: dict) -> Position:
        symbol = r.get("symbol", "")
        return Position(
            symbol=reverse_sym(symbol),
            direction="LONG" if str(r.get("type", "")).lower() == "buy" else "SHORT",
            entry_price=float(r.get("price_open", 0) or 0),
            current_price=float(r.get("price_current", 0) or 0),
            quantity=float(r.get("volume", 0) or 0),
            pnl=float(r.get("profit", 0) or 0),
            id=str(r.get("ticket", "") or ""),
        )
