"""FX-7 live-контур — адаптация engine/paper.py под forex (спека fx7_live_spec.md, D.Этап 3).

Паттерн взят из engine/paper.py (модуль crypto LSR-CROSS): состояние — PG
multi_state (id=1), dry-run → scratch schema ``strategies_replay`` (live-строки
не трогаются), честные циклы run_detect/run_tick. Детект и тик — FX-7 свои:

  run_detect(ctx) — детект кандидатов (strategies/fx7/detect.py) с прогоном через
    единый engine.gates.apply_gates (гейты live=backtest); кластеры читаются из
    PG multi_state.clusters (R3). Прошедшие сигналы → ENTER в {schema}.mt5_signals
    (write_mt5_signals) с common-pool sizing лота.
  run_tick(ctx) — 1) sync MT5→PG (sync_mt5, сохраняет pyramid_added/entry_time),
    2) MTM: SL/TP/trailing/timeout по позициям (цены через executor.get_price),
    3) dd_stop, 4) закрытие через executor.close_position (EXIT-сигнал).

Состояние: {schema}.multi_state; live → fx_top, dry-run → strategies_replay.
Все времена — UTC. Net/sma72/mom3d для CH-гейтов (R5) инъецируются через
ctx.gates_ctx: пока не заданы — гейты fail-closed (R10), входы не эмитятся.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import psycopg2

from tqa_framework.engine.exchange_base import ExchangeBase, Position, Signal
from tqa_framework.engine.exchange_mt5_bridge import (
    DEFAULT_PG,
    ensure_mt5_schema,
    insert_signal,
    mark_processed,
    mt5_sym,
    next_signal_id,
    reverse_sym,
)
from tqa_framework.engine.gates import (
    GateConfig,
    GateContext,
    apply_gates,
    sig_from_signal,
)
from strategies.fx7 import cluster_state
from strategies.fx7.bridge import sync_mt5, write_mt5_signals
from strategies.fx7.detect import SIGNAL_TO_FX7, detect
from strategies.fx7.detect_all import GatesContext

logger = logging.getLogger("tqa.fx7")

# ─────────────────────────────────────────────────────────────────────────────
# Конфиг FX-7 (дефолты; YAML стратегии перекрывает)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PORTFOLIO = ["euraud", "eurgbp", "eurusd", "audusd", "usdcad", "gbpusd", "usdchf"]
DEFAULT_RISK_PCT = 0.47        # риск на сделку, % от equity
DEFAULT_MAX_CONC = 8           # целевое (R7: 6→8)
DEFAULT_DD_STOP_PCT = 0.25     # MTM drawdown stop
DEFAULT_COMMISSION = 0.0001
DEFAULT_SL_PIPS = 30
DEFAULT_TP_PIPS = 60
DEFAULT_HOLD_DAYS = 30.0
MIN_LOT = 0.01
MAX_LOT = 2.0
PIP_VALUE = 10.0               # $ за 1 пипс на 1.0 лот (AlfaForex)
SUFFIX = "rfd"

DDL_MULTI_CLOSED = """
CREATE TABLE IF NOT EXISTS {schema}.multi_closed_trades (
    id BIGSERIAL PRIMARY KEY,
    strategy TEXT NOT NULL DEFAULT 'fx7',
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    pnl_usd NUMERIC,
    pnl_pct NUMERIC,
    ts_open TIMESTAMPTZ,
    ts_close TIMESTAMPTZ,
    entry_price NUMERIC,
    exit_price NUMERIC,
    reason TEXT,
    bars_held INT
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Контекст одного цикла
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class LiveContext:
    """Зависимости live-цикла (инъекция для тестов).

    config — сырой YAML-конфиг стратегии; executor — ExchangeMT5 (live) или
    Fx7MockExecutor (dry-run); pg_factory/conn — подключение к PG (иначе
    DEFAULT_PG); gates_ctx — единый контекст гейтов (иначе строится из config).
    """

    config: dict = field(default_factory=dict)
    executor: Optional[ExchangeBase] = None
    schema: str = "fx_top"
    pg_factory: Optional[Callable] = None
    conn: Optional = None
    gates_ctx: Optional[GatesContext] = None
    ch: object = None
    now: Optional[datetime] = None
    symbols: Optional[list[str]] = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _norm_now(now: Optional[datetime]) -> datetime:
    if now is None:
        return now_utc()
    dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_utc(s) -> Optional[datetime]:
    """ISO-строка/наивный datetime → aware UTC (или None)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@contextmanager
def _open(ctx: LiveContext):
    """Отдать conn из ctx.conn; иначе открыть через ctx.pg_factory; иначе прод-PG."""
    if ctx.conn is not None:
        yield ctx.conn
        return
    if ctx.pg_factory is not None:
        with ctx.pg_factory() as c:
            yield c
        return
    pg = dict(DEFAULT_PG)
    pg.pop("schema", None)
    with psycopg2.connect(
        host=pg["host"], port=int(pg.get("port", 5432)), dbname=pg["dbname"],
        user=pg["user"], password=pg.get("password", ""), connect_timeout=5,
    ) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# Конфиг-хелперы
# ─────────────────────────────────────────────────────────────────────────────
def load_config_yaml(path: str) -> dict:
    """Прочитать YAML стратегии → сырой dict (None-безопасно)."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("gates", {})
    raw.setdefault("risk", {})
    return raw


def build_gates_ctx(config: dict, now: Optional[datetime] = None) -> GatesContext:
    """GatesContext из YAML-конфига (единый engine.gates для live=backtest)."""
    return GatesContext(
        config=GateConfig.from_dict(config),
        ctx=GateContext(now=now),
        raw=config,
    )


def portfolio_from_config(config: dict) -> list[str]:
    syms = [str(s["sym"]).lower() for s in config.get("symbols", []) or []
            if isinstance(s, dict) and s.get("sym")]
    return syms or list(DEFAULT_PORTFOLIO)


def sym_config_map(config: dict) -> dict:
    return {str(s["sym"]).lower(): s for s in config.get("symbols", []) or []
            if isinstance(s, dict) and s.get("sym")}


def risk_slot_frac(config: dict) -> float:
    """Доля equity под риск одной сделки.

    common_pool=true → base_risk% / max_conc (риск делится на слоты, equity/max_conc).
    """
    risk = config.get("risk") or {}
    base = float(risk.get("base_risk", DEFAULT_RISK_PCT)) / 100.0
    if risk.get("common_pool", True):
        base = base / max(1, int(risk.get("max_conc", DEFAULT_MAX_CONC)))
    return base


def size_lot(equity: float, risk_frac: float, sl_pips: float,
             pip_value: float = PIP_VALUE,
             min_lot: float = MIN_LOT, max_lot: float = MAX_LOT) -> float:
    """Common-pool лот: риск $ на сделку / (SL pips × стоимость пипса)."""
    denom = max(float(sl_pips), 1.0) * max(float(pip_value), 1.0)
    lot = equity * risk_frac / denom if equity > 0 else min_lot
    return max(min_lot, min(max_lot, round(lot, 2)))


# ─────────────────────────────────────────────────────────────────────────────
# Состояние: PG multi_state (id=1)
# ─────────────────────────────────────────────────────────────────────────────
def ensure_tables(schema: str = "fx_top", conn=None) -> None:
    """Создать multi_state(+clusters) + multi_closed_trades контура (идемпотентно)."""
    c = conn if conn is not None else cluster_state._conn(None)
    cluster_state.ensure_multi_state_clusters(schema, conn=c)
    with c.cursor() as cur:
        cur.execute(DDL_MULTI_CLOSED.format(schema=schema))
    c.commit()


def load_state(conn, schema: str = "fx_top") -> dict:
    """Прочитать multi_state id=1 → dict(equity, peak, balance, positions, pending, strategy_config)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT equity, peak, balance, positions, pending_signals, strategy_config "
            f"FROM {schema}.multi_state WHERE id=1"
        )
        row = cur.fetchone()
    if not row:
        return {"equity": 500.0, "peak": 500.0, "balance": 500.0,
                "positions": [], "pending": [], "strategy_config": {}}

    def _load(x, default):
        if x is None:
            return default
        return json.loads(x) if isinstance(x, str) else x

    return {
        "equity": float(row[0] or 500), "peak": float(row[1] or 500),
        "balance": float(row[2] or 500), "positions": _load(row[3], []),
        "pending": _load(row[4], []), "strategy_config": _load(row[5], {}),
    }


def save_state(conn, schema: str = "fx_top", *, equity: float, peak: float,
               balance: float, positions: list, pending: Optional[list] = None,
               strategy_config: Optional[dict] = None, ts: str = "") -> None:
    """Записать multi_state id=1 (peak берётся как максимум)."""
    ts = ts or now_utc().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {schema}.multi_state SET equity=%s, peak=GREATEST(peak, %s), "
            f"balance=%s, positions=%s, pending_signals=%s, strategy_config=%s, "
            f"updated_at=%s WHERE id=1",
            (round(equity, 2), round(equity, 2), round(balance, 2),
             json.dumps(positions, default=str),
             json.dumps(pending if pending is not None else [], default=str),
             json.dumps(strategy_config if strategy_config is not None else {}, default=str),
             ts),
        )
    conn.commit()


def seed_state(conn, schema: str = "fx_top", equity: float = 500.0,
               ts: str = "") -> None:
    """Сид-строка multi_state id=1 (upsert-семантика — refresh после ресета)."""
    ts = ts or now_utc().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.multi_state "
            f"(id, equity, peak, balance, positions, pending_signals, strategy_config, updated_at) "
            f"VALUES (1, %s, %s, %s, '[]'::jsonb, '[]'::jsonb, '{{}}'::jsonb, %s) "
            f"ON CONFLICT (id) DO UPDATE SET equity=EXCLUDED.equity, peak=EXCLUDED.peak, "
            f"balance=EXCLUDED.balance, positions=EXCLUDED.positions, "
            f"pending_signals=EXCLUDED.pending_signals, strategy_config=EXCLUDED.strategy_config, "
            f"updated_at=EXCLUDED.updated_at",
            (round(equity, 2), round(equity, 2), round(equity, 2), ts),
        )
    conn.commit()


def _parse_recents(raw) -> dict:
    """strategy_config.recent_enters {'sym|DIR': ts} → {(sym, dir): ts}."""
    out = {}
    for k, v in (raw or {}).items():
        sym, _, direction = str(k).partition("|")
        out[(sym, direction)] = v
    return out


def _dump_recents(recents: dict) -> dict:
    return {f"{k[0]}|{k[1]}": v for k, v in recents.items()}


def _pair_cl(conn, schema: str) -> dict:
    """{sym: consecutive_losses} из mt5_loss_tracker (если таблица есть)."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT symbol, consecutive_losses FROM {schema}.mt5_loss_tracker")
            return {str(r[0]).lower(): int(r[1] or 0) for r in cur.fetchall()}
    except Exception:  # noqa: BLE001 — скретч без таблицы → нет статистики
        return {}


def _last_trade(conn, schema: str) -> Optional[tuple]:
    """Последняя закрытая сделка (direction, pnl_usd) для gate_last_profit_opposite."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT direction, pnl_usd FROM {schema}.multi_closed_trades "
                f"ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    return (str(row[0]), float(row[1]) if row[1] is not None else None)


# ─────────────────────────────────────────────────────────────────────────────
# run_detect: детект → гейты (кластеры из PG) → ENTER в mt5_signals
# ─────────────────────────────────────────────────────────────────────────────
_CLUSTER_RE = re.compile(r"cluster=([A-Za-z]+)@([\d.]+)")


def _cluster_alive(sig: Signal, clusters: dict, zr: float = 0.005) -> bool:
    """Жив ли кластер сигнала в PG multi_state.clusters (R3: состояние из PG)."""
    m = _CLUSTER_RE.search(sig.reason or "")
    if not m:
        return True  # нет инфо о кластере — не блокируем (fail-open)
    ctype, clevel = m.group(1), float(m.group(2))
    for c in clusters.values():
        if c.get("status") != "active":
            continue
        if c.get("type") != ctype:
            continue
        if abs(float(c.get("level", 0) or 0) - clevel) > zr * 0.5:
            continue
        return True
    return False


def _current_price(ctx: LiveContext, sym: str) -> Optional[float]:
    """Текущая цена символа через executor (bridge/CH) → None при недоступности."""
    ex = ctx.executor
    if ex is None:
        return None
    try:
        px = float(ex.get_price(str(sym).upper()))
    except Exception:  # noqa: BLE001 — bridge недоступен → fail-soft
        return None
    return px if px and px > 0 else None


def run_detect(ctx: LiveContext) -> dict:
    """Один detect-цикл live-контура.

    Детект по портфелю, прогон через единый engine.gates (кластеры — из PG),
    прошедшие → ENTER в {schema}.mt5_signals (write_mt5_signals). Возвращает
    {detected, entered, ids, blocked}.
    """
    now = _norm_now(ctx.now)
    symbols = ctx.symbols or portfolio_from_config(ctx.config)
    schema = ctx.schema
    gctx = ctx.gates_ctx or build_gates_ctx(ctx.config, now)
    sym_cfg = sym_config_map(ctx.config)
    zr = float(next(iter(sym_cfg.values()), {}).get("zr", 0.005))

    with _open(ctx) as conn:
        state = load_state(conn, schema)
        clusters = cluster_state.load_clusters(schema, conn=conn)
        scfg = dict(state.get("strategy_config") or {}) if isinstance(state.get("strategy_config"), dict) else {}
        positions = list(state.get("positions") or [])

        # ── окружение гейтов из live-состояния ──
        gctx.ctx.now = now
        gctx.ctx.positions = positions
        gctx.ctx.recent_enters = _parse_recents(scfg.get("recent_enters"))
        gctx.ctx.pair_cl = _pair_cl(conn, schema)
        gctx.ctx.last_trade = _last_trade(conn, schema)

        equity = float(state.get("equity") or 0) or 500.0
        risk_cfg = ctx.config.get("risk") or {}
        max_conc = int(risk_cfg.get("max_conc", DEFAULT_MAX_CONC))
        risk_frac = risk_slot_frac(ctx.config)
        sl_pips = int(risk_cfg.get("sl_pips", DEFAULT_SL_PIPS))
        tp_pips = int(risk_cfg.get("tp_pips", DEFAULT_TP_PIPS))

        entered: list[dict] = []
        blocked: list[dict] = []
        detected = 0
        for sym in symbols:
            gctx.ctx.price = _current_price(ctx, sym)
            signals = detect([sym], now, config=gctx.raw, ch=ctx.ch)
            detected += len(signals)
            for sig in signals:
                gsig = sig_from_signal(sig, direction_map=SIGNAL_TO_FX7)
                gctx.ctx.cluster_alive = _cluster_alive(sig, clusters, zr)
                blk, reason = apply_gates(gsig, gctx.ctx, gctx.config)
                if blk:
                    blocked.append({"sym": sym, "direction": gsig["direction"], "reason": reason})
                    continue
                if len(positions) + len(entered) >= max_conc:
                    blocked.append({"sym": sym, "direction": gsig["direction"],
                                    "reason": f"max_conc={max_conc}"})
                    continue
                entered.append({
                    "sym": sym,
                    "direction": gsig["direction"],
                    "entry_price": float(gsig["entry_price"]),
                    "sl_pips": sl_pips,
                    "tp_pips": tp_pips,
                    "lot": size_lot(equity, risk_frac, sl_pips),
                })

        ids = (write_mt5_signals(entered, conn=conn, pg_factory=ctx.pg_factory, schema=schema)
               if entered else [])

        recents = _parse_recents(scfg.get("recent_enters"))
        for rec, sid in zip(entered, ids):
            recents[(rec["sym"], rec["direction"])] = now.isoformat()[:16]
        scfg["recent_enters"] = _dump_recents(recents)
        state["pending"] = list(state.get("pending") or []) + [
            {"sym": r["sym"], "direction": r["direction"],
             "entry_price": r["entry_price"], "id": sid, "ts_added": now.isoformat()}
            for r, sid in zip(entered, ids)
        ]
        save_state(conn, schema, equity=state["equity"], peak=state["peak"],
                   balance=state["balance"], positions=positions,
                   pending=state["pending"], strategy_config=scfg, ts=now.isoformat())

    logger.info("[FX7 detect] symbols=%d detected=%d entered=%d blocked=%d",
                len(symbols), detected, len(entered), len(blocked))
    return {"detected": detected, "entered": entered,
            "ids": ids, "blocked": blocked, "symbols": symbols}


# ─────────────────────────────────────────────────────────────────────────────
# run_tick: sync MT5→PG + MTM (SL/TP/trailing/timeout) + dd_stop
# ─────────────────────────────────────────────────────────────────────────────
_PIP_SIZES = {"jpy": 0.01, "xau": 0.1}


def _pip_size(sym: str) -> float:
    for k, v in _PIP_SIZES.items():
        if k in sym.lower():
            return v
    return 0.0001


def _pnl_pct(pos: dict, exit_px: float, comm: float) -> float:
    entry = float(pos.get("entry_price") or 0) or 1.0
    d = 1 if pos["direction"] == "BUY" else -1
    move = (exit_px - entry) / entry * d
    return move - comm


def _make_trade(pos: dict, exit_px: float, reason: str, pnl_usd: float,
                pnl_pct: float, now: datetime) -> dict:
    sym = str(pos.get("sym", "")).lower()
    entry = float(pos.get("entry_price") or 0)
    et = pos.get("entry_time") or ""
    d0 = _to_utc(et)
    hold_h = round((now - d0).total_seconds() / 3600, 1) if d0 else 0.0
    return {
        "sym": sym, "direction": pos.get("direction", ""),
        "pnl_usd": round(pnl_usd, 2), "pnl_pct": round(pnl_pct, 4),
        "pnl_pips": round((exit_px - entry) / max(_pip_size(sym), 1e-12), 1),
        "entry": round(entry, 6), "exit": round(float(exit_px), 6),
        "entry_time": et, "exit_time": now.isoformat(),
        "reason": reason, "hold_hours": hold_h,
        "detail": f"{sym} {pos.get('direction', '')} {reason} pnl={pnl_usd:+.2f}$",
    }


def _scan_price(pos: dict, price: float, cfg: dict, now: datetime) -> tuple:
    """Один тик позиции: trailing → SL → TP → timeout.

    Возвращает (exit_px, reason) или (None, None) — держим. Порядок как в
    paper.scan_position: при активном трейле TP не срабатывает.
    """
    entry = float(pos.get("entry_price") or 0)
    if entry <= 0:
        return None, None
    d = 1 if pos["direction"] == "BUY" else -1
    sl_pct = float(cfg.get("sl_pct", 1.5))
    tp_pct = float(cfg.get("tp_pct", 2.0))
    sl_px = entry * (1 - sl_pct / 100) if d == 1 else entry * (1 + sl_pct / 100)
    tp_px = entry * (1 + tp_pct / 100) if d == 1 else entry * (1 - tp_pct / 100)

    trail_act = float(pos.get("trail_activation") or 0)   # % от entry
    trail_dist = float(pos.get("trail_trail") or 0)
    trail_active = bool(pos.get("trail_active"))
    trail_peak = float(pos.get("trail_peak") or entry)
    if trail_act > 0:
        if not trail_active:
            if (d == 1 and price >= entry * (1 + trail_act / 100)) or \
               (d == -1 and price <= entry * (1 - trail_act / 100)):
                trail_active = True
                trail_peak = price
        if trail_active:
            if d == 1:
                if price > trail_peak:
                    trail_peak = price
                sl_px = max(sl_px, trail_peak * (1 - trail_dist / 100), entry)
            else:
                if price < trail_peak:
                    trail_peak = price
                sl_px = min(sl_px, trail_peak * (1 + trail_dist / 100), entry)
    pos["trail_active"] = trail_active
    pos["trail_peak"] = trail_peak

    if d == 1:
        if price <= sl_px:
            return sl_px, ("trail" if trail_active else "sl")
        if not trail_active and price >= tp_px:
            return tp_px, "tp"
    else:
        if price >= sl_px:
            return sl_px, ("trail" if trail_active else "sl")
        if not trail_active and price <= tp_px:
            return tp_px, "tp"

    hold_days = float(cfg.get("hold_days", DEFAULT_HOLD_DAYS))
    et = _to_utc(pos.get("entry_time"))
    if et and (now - et) >= timedelta(days=hold_days):
        return price, "timeout"
    return None, None


def _suffix_of(ex) -> str:
    params = getattr(getattr(ex, "config", None), "params", {}) or {}
    return str((params.get("mt5") or {}).get("symbol_suffix")
               or params.get("symbol_suffix") or SUFFIX)


def _pos_to_mt5(p: Position, ex=None) -> dict:
    """Position (наш формат) → строка mt5_account.positions (формат bridge)."""
    return {
        "symbol": mt5_sym(str(p.symbol), _suffix_of(ex)),
        "type": "buy" if str(p.direction).upper() == "LONG" else "sell",
        "volume": float(getattr(p, "quantity", MIN_LOT) or MIN_LOT),
        "price_open": float(p.entry_price or 0),
        "price_current": float(p.current_price or p.entry_price or 0),
        "profit": float(getattr(p, "pnl", 0) or 0),
        "ticket": str(p.id or ""),
    }


def _close_via_executor(ex, pos: dict, exit_px: float) -> None:
    """EXIT через executor (EXIT-сигнал + bridge.close_symbol для live)."""
    try:
        ex.close_position(Position(
            symbol=str(pos.get("sym", "")).upper(),
            direction="LONG" if pos.get("direction", "") == "BUY" else "SHORT",
            entry_price=float(pos.get("entry_price") or 0),
            current_price=float(exit_px),
            quantity=float(pos.get("lot") or MIN_LOT),
            id=str(pos.get("ticket") or ""),
        ))
    except Exception as e:  # noqa: BLE001 — EXIT-сигнал уже в PG либо bridge закрыл сам
        logger.warning("[FX7] close %s failed: %s", pos.get("sym"), e)


def _insert_closed_trade(conn, schema: str, t: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.multi_closed_trades "
            f"(strategy, symbol, direction, pnl_usd, pnl_pct, ts_open, ts_close, "
            f"entry_price, exit_price, reason, bars_held) "
            f"VALUES ('fx7', %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)",
            (t["sym"], t["direction"], t["pnl_usd"], t["pnl_pct"],
             t["entry_time"], t["exit_time"], t["entry"], t["exit"], t["reason"]),
        )
    conn.commit()


def run_tick(ctx: LiveContext) -> dict:
    """Один тик live-контура: sync MT5→PG, MTM (SL/TP/trail/timeout), dd_stop.

    Возвращает {opened, closed, closed_mt5, equity, peak, balance, positions, trades}.
    """
    now = _norm_now(ctx.now)
    schema = ctx.schema
    ex = ctx.executor
    if ex is None:
        raise ValueError("run_tick: ctx.executor обязателен (ExchangeMT5 или mock)")

    risk_cfg = ctx.config.get("risk") or {}
    risk_frac = risk_slot_frac(ctx.config)
    comm = float(risk_cfg.get("comission", DEFAULT_COMMISSION))
    dd_stop_pct = float(risk_cfg.get("dd_stop_pct", DEFAULT_DD_STOP_PCT))
    sym_cfg = sym_config_map(ctx.config)

    with _open(ctx) as conn:
        state = load_state(conn, schema)
        opened = 0
        if hasattr(ex, "consume_signals"):  # dry-run: ENTER-сигналы → mt5_account
            opened = int(ex.consume_signals())
        mt5_rows = [_pos_to_mt5(p, ex) for p in ex.get_positions()]
        merged = sync_mt5(state, mt5_rows)  # MT5 → PG (pyramid/entry_time сохранены)
        positions = list(merged.get("positions") or [])
        for pos in positions:  # state хранит sym в нижнем регистре (как детект/гейты)
            pos["sym"] = str(pos.get("sym", "")).lower()
        closed_mt5 = list(merged.get("closed_trades") or [])

        equity = float(merged.get("equity") or 0) or \
            float(state.get("equity") or 0) or 500.0
        peak = float(merged.get("peak") or 0) or \
            max(equity, float(state.get("peak") or 0) or equity)
        balance = float(merged.get("balance") or 0) or \
            float(state.get("balance") or 0) or equity

        if ex is not None:
            prices = {}
            for pos in positions:
                sym = str(pos.get("sym", "")).lower()
                px = _current_price(ctx, sym)
                if px and px > 0:
                    prices[sym] = px

        closed_trades: list[dict] = []
        remaining: list[dict] = []
        for pos in positions:
            sym = str(pos.get("sym", "")).lower()
            px = prices.get(sym)
            if not px:
                remaining.append(pos)
                continue
            exit_px, reason = _scan_price(pos, px, sym_cfg.get(sym, {}), now)
            if not reason:
                remaining.append(pos)
                continue
            pnl_pct = _pnl_pct(pos, px, comm)
            pnl_usd = equity * risk_frac * pnl_pct
            equity += pnl_usd
            peak = max(peak, equity)
            trade = _make_trade(pos, px, reason, pnl_usd, pnl_pct, now)
            closed_trades.append(trade)
            _insert_closed_trade(conn, schema, trade)
            _close_via_executor(ex, pos, px)
            logger.info("[FX7 CLOSE] %s %s (%s) pnl=%+.2f$ eq=%.2f$",
                        sym, pos["direction"], reason, pnl_usd, equity)

        # MTM drawdown stop
        if remaining:
            mtm_dd = 0.0
            for pos in remaining:
                px = prices.get(str(pos.get("sym", "")).lower())
                if not px:
                    continue
                entry = float(pos.get("entry_price") or 0)
                if entry <= 0:
                    continue
                d = 1 if pos["direction"] == "BUY" else -1
                dd_frac = max(0.0, (entry - px) / entry if d == 1 else (px - entry) / entry)
                mtm_dd += risk_frac * dd_frac
            eq_mtm = equity * (1 - mtm_dd)
            dd_pct = (peak - eq_mtm) / peak * 100 if peak > 0 else 0.0
            if dd_pct > dd_stop_pct * 100:
                logger.warning("[FX7 DD-STOP] MTM DD %.1f%% > %.0f%% — close all %d",
                               dd_pct, dd_stop_pct * 100, len(remaining))
                for pos in list(remaining):
                    sym = str(pos.get("sym", "")).lower()
                    px = prices.get(sym)
                    if not px:
                        remaining.remove(pos)
                        continue
                    pnl_pct = _pnl_pct(pos, px, comm)
                    pnl_usd = equity * risk_frac * pnl_pct
                    equity += pnl_usd
                    peak = max(peak, equity)
                    trade = _make_trade(pos, px, "dd_stop", pnl_usd, pnl_pct, now)
                    closed_trades.append(trade)
                    _insert_closed_trade(conn, schema, trade)
                    _close_via_executor(ex, pos, px)
                    remaining.remove(pos)

        save_state(conn, schema, equity=equity, peak=peak, balance=balance,
                   positions=remaining, pending=state.get("pending") or [],
                   strategy_config=state.get("strategy_config") or {},
                   ts=now.isoformat())

    return {"opened": opened, "closed": len(closed_trades) - len(closed_mt5),
            "closed_mt5": len(closed_mt5), "equity": round(equity, 2),
            "peak": round(peak, 2), "balance": round(balance, 2),
            "positions": len(remaining), "trades": closed_mt5 + closed_trades}


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run исполнитель (mock): ENTER → mt5_account.positions без Wine
# ─────────────────────────────────────────────────────────────────────────────
class Fx7MockExecutor(ExchangeBase):
    """Dry-run исполнитель: ведёт себя как Wine-bridge, но только в scratch schema.

    consume_signals() — читает необработанные ENTER из {schema}.mt5_signals и
    кладёт позиции в {schema}.mt5_account.positions (как это сделал бы bridge).
    get_price читает self.prices (тесты/тик задают напрямую). Никаких сетей.
    """

    def __init__(self, config, pg_factory: Callable, schema: str = "strategies_replay"):
        super().__init__(config)
        self._pg_factory = pg_factory
        self._schema = schema
        params = config.params or {}
        self._suffix = str((params.get("mt5") or {}).get("symbol_suffix")
                           or params.get("symbol_suffix") or SUFFIX)
        self.prices: dict[str, float] = {}

    def ensure_schema(self) -> None:
        with self._pg_factory() as conn:
            ensure_mt5_schema(conn, self._schema)

    def _account_positions(self, cur=None) -> list[dict]:
        del cur
        try:
            with self._pg_factory() as conn:
                cur2 = conn.cursor()
                cur2.execute(f"SELECT positions FROM {self._schema}.mt5_account WHERE id=1")
                row = cur2.fetchone()
                conn.commit()
        except Exception:  # noqa: BLE001 — таблицы может не быть до первого ensure
            return []
        if not row or not row[0]:
            return []
        return row[0] if isinstance(row[0], list) else json.loads(row[0])

    def _save_positions(self, rows: list[dict]) -> None:
        with self._pg_factory() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {self._schema}.mt5_account SET positions=%s::jsonb, "
                f"positions_count=%s, updated_at=NOW() WHERE id=1",
                (json.dumps(rows), len(rows)),
            )
            conn.commit()

    # ── Wine-bridge consumer (аналог: ENTER-сигнал → позиция на MT5) ──
    def consume_signals(self) -> int:
        with self._pg_factory() as conn:
            ensure_mt5_schema(conn, self._schema)
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, action, symbol, entry, lot_size "
                f"FROM {self._schema}.mt5_signals "
                f"WHERE type='ENTER' AND processed_at IS NULL ORDER BY created_at"
            )
            sigs = cur.fetchall()
            if not sigs:
                return 0
            rows = self._account_positions()
            for rid, action, symbol, entry, lot in sigs:
                rows.append({
                    "ticket": rid,
                    "symbol": mt5_sym(str(symbol), self._suffix),
                    "type": "buy" if str(action).upper() == "BUY" else "sell",
                    "volume": float(lot or MIN_LOT),
                    "price_open": float(entry or 0),
                    "price_current": float(entry or 0),
                    "profit": 0.0, "swap": 0.0,
                })
                mark_processed(conn, self._schema, rid)
            self._save_positions(rows)
            return len(sigs)

    # ── ExchangeBase ──
    def get_price(self, symbol: str) -> float:
        return float(self.prices.get(str(symbol).upper(), 0.0))

    def get_positions(self) -> list[Position]:
        return [self._row_to_position(r) for r in self._account_positions()]

    def _row_to_position(self, r: dict) -> Position:
        return Position(
            symbol=reverse_sym(str(r.get("symbol", ""))).upper(),
            direction="LONG" if str(r.get("type", "")).lower() == "buy" else "SHORT",
            entry_price=float(r.get("price_open", 0) or 0),
            current_price=float(r.get("price_current", 0) or 0),
            quantity=float(r.get("volume", 0) or 0),
            pnl=float(r.get("profit", 0) or 0),
            id=str(r.get("ticket", "") or ""),
        )

    def open_position(self, signal: Signal, quantity: float) -> Optional[Position]:
        if signal is None or not signal.symbol:
            return None
        action = "BUY" if signal.direction.upper() == "LONG" else "SELL"
        lot = max(MIN_LOT, round(float(quantity) / 0.01) * 0.01)
        sig_id = next_signal_id()
        with self._pg_factory() as conn:
            ensure_mt5_schema(conn, self._schema)
            insert_signal(conn, self._schema, dict(
                id=sig_id, type="ENTER", action=action, symbol=signal.symbol.upper(),
                entry=float(signal.price), sl_pips=30, tp_pips=60, lot_size=lot,
            ))
            rows = self._account_positions()
            rows.append({
                "ticket": sig_id, "symbol": mt5_sym(signal.symbol.upper(), self._suffix),
                "type": "buy" if action == "BUY" else "sell",
                "volume": lot, "price_open": float(signal.price),
                "price_current": float(signal.price), "profit": 0.0, "swap": 0.0,
            })
            self._save_positions(rows)
        return Position(symbol=signal.symbol.upper(), direction=signal.direction.upper(),
                        entry_price=float(signal.price), current_price=float(signal.price),
                        quantity=lot, id=sig_id)

    def close_position(self, position: Position) -> bool:
        if position is None or not position.symbol:
            return False
        sym = mt5_sym(position.symbol.upper(), self._suffix).upper()
        rows = [r for r in self._account_positions()
                if str(r.get("ticket", "")).lower() != str(position.id or "").lower()
                and str(r.get("symbol", "")).upper() != sym]
        self._save_positions(rows)
        return True

    def get_account_balance(self) -> float:
        with self._pg_factory() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT equity FROM {self._schema}.mt5_account WHERE id=1")
            row = cur.fetchone()
            conn.commit()
        return float(row[0] or 0) if row else 0.0