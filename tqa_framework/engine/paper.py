"""Paper trader — live-contour LSR-CROSS detect+tick.

Replicates the live TQA-crypto LSR-CROSS paper trader 1:1.
State persisted to PG ``strategies.multi_state`` (id=1).
Closed trades appended to ``strategies.multi_closed_trades``.

Scratch override (--dry-run): creates ``strategies_replay.*`` tables so
the live row id=1 and live multi_closed_trades are never modified.
"""
from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timezone, timedelta

import clickhouse_connect
import psycopg2
import psycopg2.extras

logger = logging.getLogger("tqa.paper")

SCAN_MINUTES = 60
MAX_LOOKBACK_H = 12

# ─── CH / PG defaults (live infra) ──────────────────────────────────────
DEFAULT_CH = dict(host="10.0.0.60", port=8123, database="crypto")
DEFAULT_PG = dict(
    host="10.0.0.60",
    port=5432,
    dbname="crypto",
    user="user",
    password=os.environ.get("CRYPTO_DB_PASSWORD", ""),
)

# ─── Default strategy parameters ────────────────────────────────────────
DEFAULT_PARAMS = dict(
    base_risk=0.08,
    leverage=1.0,
    max_pos=6,
    sl_pct=0.05,
    tp_pct=0.18,
    comm=0.001,
    slip=0.0005,
    trail_act=0.05,
    trail_dist=0.06,
    trail_lock=0.05,
    pyr_trigger=0.05,
    pyr_add=0.5,
    dd_stop_pct=0.25,
    timeout_bars=120,
    max_margin_ratio=0.8,
    th=2.0,
    z_window=720,
    exclude_hours=[11, 22, 23],
    exclude_dow=[2],
    exclude_syms=["SOLUSDT"],
)

DEFAULT_SYMBOLS = [
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT",
    "AVAXUSDT", "LINKUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
]

DEFAULT_SYM_RISK = {
    "ADAUSDT": 1.3, "ARBUSDT": 1.2, "OPUSDT": 1.1, "AVAXUSDT": 1.05,
    "DOGEUSDT": 1.0, "XRPUSDT": 1.0, "ETHUSDT": 1.0, "SOLUSDT": 1.0,
    "BNBUSDT": 1.0, "LINKUSDT": 1.0, "NEARUSDT": 0.9, "APTUSDT": 0.9,
}

DDL_MULTI_STATE = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.multi_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    equity NUMERIC DEFAULT 500.0,
    peak NUMERIC DEFAULT 500.0,
    balance NUMERIC DEFAULT 500.0,
    positions JSONB DEFAULT '[]'::jsonb,
    pending_signals JSONB DEFAULT '[]'::jsonb,
    strategy_config JSONB DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

DDL_MULTI_CLOSED = """
CREATE TABLE IF NOT EXISTS {schema}.multi_closed_trades (
    id BIGSERIAL PRIMARY KEY,
    strategy TEXT NOT NULL DEFAULT 'dragon',
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

DDL_MULTI_STATE_SEED = """
INSERT INTO {schema}.multi_state (id, equity, peak, balance, positions, pending_signals, strategy_config)
VALUES (1, %s, %s, %s, '[]'::jsonb, '[]'::jsonb, '{{}}'::jsonb)
ON CONFLICT (id) DO NOTHING;
"""


# ─── YAML config loader ─────────────────────────────────────────────────

def load_config(yaml_path: str) -> dict:
    """Load YAML strategy config → flat cfg dict matching live semantics.

    Config priority: YAML params → defaults.  ``sym_risk`` is read from
    ``risk.sym_risk`` or top-level ``sym_risk``.
    """
    import yaml

    cfg = dict(DEFAULT_PARAMS)
    cfg["symbols"] = list(DEFAULT_SYMBOLS)
    cfg["sym_risk"] = dict(DEFAULT_SYM_RISK)

    try:
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("YAML %s not found, using defaults", yaml_path)
        return cfg
    if not isinstance(raw, dict):
        return cfg

    # params section
    params = raw.get("params", {})
    if isinstance(params, dict):
        for k, v in params.items():
            cfg[k] = v

    # signals[].params — strategy_engine places per-signal risk/trail params
    # here (trail_act/dist/lock, sl_pct, tp_pct, pyr_*, hold_h). Both lsr_short
    # and lsr_long carry identical params, so take from the first present.
    signals = raw.get("signals", {})
    if isinstance(signals, dict):
        signals = list(signals.values())
    if isinstance(signals, list):
        for sg in signals:
            sparams = sg.get("params", {}) if isinstance(sg, dict) else {}
            if not isinstance(sparams, dict):
                continue
            for k, v in sparams.items():
                if k == "hold_h":
                    cfg["timeout_bars"] = v
                elif k in (
                    "sl_pct", "tp_pct", "trail_act", "trail_dist",
                    "trail_lock", "pyr_trigger", "pyr_add",
                ):
                    cfg[k] = v
            break

    # risk section
    risk = raw.get("risk", {})
    if isinstance(risk, dict):
        for k in ("base_risk", "leverage", "max_pos", "dd_stop_pct", "max_margin_ratio"):
            if k in risk:
                cfg[k] = risk[k]
        sym_risk = risk.get("sym_risk")
        if isinstance(sym_risk, dict):
            cfg["sym_risk"] = {k: float(v) for k, v in sym_risk.items()}
        syms = risk.get("symbols")
        if isinstance(syms, list):
            cfg["symbols"] = syms

    # comission / slippage → comm / slip
    if "comission" in risk:
        cfg["comm"] = float(risk["comission"])
    if "slippage" in risk:
        cfg["slip"] = float(risk["slippage"])

    # symbols (top-level)
    syms_top = raw.get("symbols")
    if isinstance(syms_top, list):
        cfg["symbols"] = syms_top

    # exclude from top-level exclude block
    exc = raw.get("exclude", {})
    if isinstance(exc, dict):
        if "syms" in exc:
            cfg["exclude_syms"] = list(exc["syms"])
        if "hours" in exc:
            cfg["exclude_hours"] = list(exc["hours"])
        if "dow" in exc:
            cfg["exclude_dow"] = list(exc["dow"])

    # Convert lists to sets where needed
    cfg["exclude_syms"] = set(cfg.get("exclude_syms", []))
    cfg["exclude_hours"] = set(cfg.get("exclude_hours", []))
    cfg["exclude_dow"] = set(cfg.get("exclude_dow", []))

    # Ensure numeric
    for k in ("base_risk", "leverage", "sl_pct", "tp_pct", "comm", "slip",
              "trail_act", "trail_dist", "trail_lock", "pyr_trigger", "pyr_add",
              "dd_stop_pct", "max_margin_ratio", "th", "timeout_bars"):
        cfg[k] = float(cfg[k])
    cfg["max_pos"] = int(cfg["max_pos"])
    cfg["timeout_bars"] = int(cfg["timeout_bars"])
    cfg["z_window"] = int(cfg.get("z_window", 720))

    return cfg


# ─── CH helpers ──────────────────────────────────────────────────────────

def get_lsr_bars(symbols: list[str], lookback_h: int,
                 ch_cfg: dict | None = None) -> dict[str, list]:
    """Load last-lookback_h hours of LSR data from CH long_short_ratio.

    Returns {symbol: [(ts, ratio), ...]} sorted by timestamp.
    """
    if not symbols:
        return {}
    ch_cfg = ch_cfg or DEFAULT_CH
    ch = clickhouse_connect.get_client(**ch_cfg)
    placeholders = ", ".join(["%s"] * len(symbols))
    rows = ch.query(
        f"SELECT symbol, timestamp, ratio FROM long_short_ratio "
        f"WHERE symbol IN ({placeholders}) "
        f"AND source='bybit_global' "
        f"AND timestamp >= now() - INTERVAL {lookback_h} HOUR "
        f"ORDER BY timestamp",
        parameters=symbols,
    ).result_rows
    ch.close()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r[0], []).append((r[1], float(r[2])))
    return out


def get_bars(syms: list[str], minutes: int = SCAN_MINUTES,
             ch_cfg: dict | None = None) -> dict[str, list[tuple]]:
    """Load 1m bars (ts, hi, lo, cl) for the last ``minutes`` minutes."""
    if not syms:
        return {}
    ch_cfg = ch_cfg or DEFAULT_CH
    ch = clickhouse_connect.get_client(**ch_cfg)
    placeholders = ", ".join(["%s"] * len(syms))
    rows = ch.query(
        f"SELECT symbol, timestamp, high, low, close FROM klines_1m "
        f"WHERE symbol IN ({placeholders}) "
        f"AND timestamp >= (SELECT max(timestamp) FROM klines_1m) "
        f"- INTERVAL {minutes} MINUTE "
        f"ORDER BY timestamp",
        parameters=syms,
    ).result_rows
    ch.close()
    out: dict[str, list[tuple]] = {}
    for r in rows:
        out.setdefault(r[0], []).append((r[1], float(r[2]), float(r[3]), float(r[4])))
    return out


def compute_z(lsr_data: list[tuple], z_window: int) -> list[tuple]:
    """Compute z-score for a list of (timestamp, ratio) tuples.

    Returns list of (timestamp, z) for entries with enough history.
    """
    if len(lsr_data) < 100:
        return []
    ratios = [r[1] for r in lsr_data]
    result = []
    for i in range(len(ratios)):
        win_start = max(0, i - z_window + 1)
        win = ratios[win_start : i + 1]
        if len(win) < 100:
            continue
        mean = sum(win) / len(win)
        var = sum((x - mean) ** 2 for x in win) / len(win)
        std = var ** 0.5
        if std == 0:
            continue
        z = (ratios[i] - mean) / std
        result.append((lsr_data[i][0], z))
    return result


def detect_cross_events(z_series: list[tuple], th: float) -> list[tuple]:
    """Detect LSR z-score cross events.

    Cross UP (zprev < th & z >= th) → crowd is LONG → signal SHORT.
    Cross DOWN (zprev > -th & z <= -th) → crowd is SHORT → signal LONG.

    Returns list of (timestamp, direction_str, z_score).
    """
    if len(z_series) < 2:
        return []
    events = []
    for i in range(1, len(z_series)):
        ts, z = z_series[i]
        _prev_ts, zprev = z_series[i - 1]
        if zprev < th and z >= th:
            events.append((ts, "SHORT", z))
        elif zprev > -th and z <= -th:
            events.append((ts, "LONG", z))
    return events


# ─── State management ────────────────────────────────────────────────────

def _connect_pg(pg_cfg: dict | None = None):
    """Create a raw psycopg2 connection."""
    pg_cfg = pg_cfg or DEFAULT_PG
    return psycopg2.connect(**pg_cfg)


def ensure_scratch(pg_cfg: dict | None = None, schema: str = "strategies_replay"):
    """Create scratch schema + tables + seed state."""
    pg = _connect_pg(pg_cfg)
    cur = pg.cursor()
    cur.execute(DDL_MULTI_STATE.format(schema=schema))
    cur.execute(DDL_MULTI_CLOSED.format(schema=schema))
    pg.commit()
    cur.close()
    pg.close()


def ensure_live(pg_cfg: dict | None = None):
    """Ensure the live strategies schema + tables exist."""
    pg = _connect_pg(pg_cfg)
    cur = pg.cursor()
    cur.execute(DDL_MULTI_STATE.format(schema="strategies"))
    cur.execute(DDL_MULTI_CLOSED.format(schema="strategies"))
    pg.commit()
    cur.close()
    pg.close()


def load_state(cur, schema: str = "strategies") -> tuple[float, float, float, list]:
    """Load (equity, peak, balance, positions) from multi_state."""
    cur.execute(
        f"SELECT equity, peak, balance, positions "
        f"FROM {schema}.multi_state WHERE id=1"
    )
    row = cur.fetchone()
    if row:
        return (
            float(row[0] or 500),
            float(row[1] or 500),
            float(row[2] or 500),
            json.loads(row[3]) if isinstance(row[3], str) else (row[3] or []),
        )
    return 500.0, 500.0, 500.0, []


def load_pending(cur, schema: str = "strategies") -> list[dict]:
    """Load pending_signals from multi_state."""
    cur.execute(
        f"SELECT pending_signals FROM {schema}.multi_state WHERE id=1"
    )
    prow = cur.fetchone()
    prow0 = prow[0] if prow else None
    return json.loads(prow0) if isinstance(prow0, str) else (prow0 or [])


def save_state(cur, equity: float, peak: float, balance: float,
               positions: list[dict], pending: list[dict] | None = None,
               schema: str = "strategies", ts: str = ""):
    """Save state to multi_state."""
    if not ts:
        ts = datetime.now(timezone.utc).isoformat()
    cur.execute(
        f"UPDATE {schema}.multi_state "
        f"SET equity=%s, peak=GREATEST(peak, %s), balance=%s, "
        f"positions=%s, updated_at=%s "
        f"WHERE id=1",
        (round(equity, 2), round(equity, 2), round(balance, 2),
         json.dumps(positions, default=str), ts),
    )
    if pending is not None:
        cur.execute(
            f"UPDATE {schema}.multi_state SET pending_signals=%s WHERE id=1",
            (json.dumps(pending),),
        )


def insert_closed_trade(cur, strategy: str, symbol: str, direction: str,
                        pnl_usd: float, pnl_pct: float,
                        ts_open: str, ts_close: str,
                        entry_price: float, exit_price: float,
                        reason: str, bars_held: int = 0,
                        schema: str = "strategies"):
    """Insert a closed trade record."""
    cur.execute(
        f"INSERT INTO {schema}.multi_closed_trades "
        f"(strategy, symbol, direction, pnl_usd, pnl_pct, "
        f"ts_open, ts_close, entry_price, exit_price, reason, bars_held) "
        f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (strategy, symbol, direction, round(pnl_usd, 2), round(pnl_pct, 4),
         ts_open, ts_close,
         round(entry_price, 6) if entry_price else 0,
         round(exit_price, 6) if exit_price else 0,
         reason, bars_held),
    )


# ─── scan_position (identical to live_tick.py) ──────────────────────────

def scan_position(entry: float, direction: str, bars: list[tuple],
                  cfg: dict, trail_peak=None,
                  trail_active: bool = False) -> tuple:
    """Scan 1m bars for exit conditions.  Identical to live scan_position.

    Order on each bar: pyr → trail → sl → tp.

    Returns (exit_px, reason, pyramid_bool, peak, trail_active).
    """
    sl_pct = cfg["sl_pct"]
    tp_pct = cfg["tp_pct"]
    trail_act = cfg["trail_act"]
    trail_dist = cfg["trail_dist"]
    trail_lock = cfg.get("trail_lock", 0.02)
    pyr_trigger = cfg["pyr_trigger"]
    d = 1 if direction == "LONG" else -1

    sl_px = entry * (1 - sl_pct) if d == 1 else entry * (1 + sl_pct)
    tp_px = entry * (1 + tp_pct) if d == 1 else entry * (1 - tp_pct)
    pyr_px = entry * (1 + pyr_trigger) if d == 1 else entry * (1 - pyr_trigger)

    pyramid = False
    peak = trail_peak if trail_peak is not None else entry

    for _ts, hi, lo, _cl in bars:
        # 1. Pyramid trigger
        if not pyramid:
            if (d == 1 and hi >= pyr_px) or (d == -1 and lo <= pyr_px):
                pyramid = True

        # 2. Trailing stop
        if trail_act is not None:
            if not trail_active:
                if (d == 1 and hi >= entry * (1 + trail_act)) or \
                   (d == -1 and lo <= entry * (1 - trail_act)):
                    trail_active = True
                    peak = hi if d == 1 else lo
                    sl_px = entry  # breakeven on activation
            if trail_active:
                if d == 1:
                    if hi > peak:
                        peak = hi
                    new_sl = peak * (1 - trail_dist)
                    if trail_lock > 0:
                        new_sl = max(new_sl, entry * (1 + trail_lock))
                    sl_px = max(sl_px, new_sl)
                else:
                    if lo < peak:
                        peak = lo
                    new_sl = peak * (1 + trail_dist)
                    if trail_lock > 0:
                        new_sl = min(new_sl, entry * (1 - trail_lock))
                    sl_px = min(sl_px, new_sl)

        # 3. SL check
        if d == 1:
            if lo <= sl_px:
                reason = "trail" if trail_active else "sl"
                return sl_px, reason, pyramid, peak, trail_active
            # 4. TP check (only when trailing not active)
            if not trail_active and hi >= tp_px:
                return tp_px, "tp", pyramid, peak, trail_active
        else:
            if hi >= sl_px:
                reason = "trail" if trail_active else "sl"
                return sl_px, reason, pyramid, peak, trail_active
            if not trail_active and lo <= tp_px:
                return tp_px, "tp", pyramid, peak, trail_active

    return None, None, pyramid, peak, trail_active


# ─── Core: run_detect ────────────────────────────────────────────────────

def run_detect(cur, cfg: dict, schema: str = "strategies",
               *, skip_cfg_write: bool = False) -> int:
    """Run LSR cross detect.  Returns number of signals added to pending.

    Replicates live_detect.py run_detect semantics.
    """
    now = datetime.now(timezone.utc)
    z_window = int(cfg.get("z_window", 720))
    th = cfg.get("th", 2.0)

    # catch-up window
    last_detect = cfg.get("last_lsr_detect")
    if last_detect:
        try:
            last_dt = datetime.fromisoformat(last_detect)
            lookback_h = min(
                MAX_LOOKBACK_H,
                max(1, (now - last_dt).total_seconds() / 3600),
            )
        except Exception:
            lookback_h = MAX_LOOKBACK_H
    else:
        lookback_h = MAX_LOOKBACK_H

    symbols = cfg.get("symbols", DEFAULT_SYMBOLS)

    # Load LSR data + compute z + detect cross events
    lsr_data = get_lsr_bars(symbols, int(round(lookback_h)))

    exclude_syms = cfg.get("exclude_syms", set())
    exclude_hours = cfg.get("exclude_hours", set())
    exclude_dow = cfg.get("exclude_dow", set())
    max_pos = cfg.get("max_pos", 6)

    # Load existing state
    equity, _peak, _balance, positions = load_state(cur, schema)
    pending = load_pending(cur, schema)

    open_syms = {p["symbol"] for p in positions}
    pending_syms = {s["symbol"] for s in pending}

    # Recently closed (protection against repeats)
    cur.execute(
        f"SELECT symbol FROM {schema}.multi_closed_trades "
        f"WHERE strategy='lsr_cross' AND ts_close > %s",
        (now - timedelta(days=7),),
    )
    recent_closed = {r[0] for r in cur.fetchall()}

    all_signals = []
    for sym in symbols:
        if sym not in lsr_data or len(lsr_data[sym]) < 100:
            continue
        z_series = compute_z(lsr_data[sym], z_window)
        events = detect_cross_events(z_series, th)
        for ts, direction, z in events:
            all_signals.append({
                "symbol": sym,
                "direction": direction,
                "ts": ts,
                "z_score": z,
            })

    # Sort by timestamp
    all_signals.sort(key=lambda x: x["ts"])

    added = 0
    for s in all_signals:
        sym = s["symbol"]
        if sym in exclude_syms:
            continue
        # Parse timestamp for hour/dow filtering
        ts_val = s["ts"]
        if isinstance(ts_val, str):
            sig_dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        elif isinstance(ts_val, datetime):
            sig_dt = ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        else:
            # Assume numeric (unix)
            sig_dt = datetime.fromtimestamp(float(ts_val), tz=timezone.utc)

        if sig_dt.hour in exclude_hours or sig_dt.weekday() in exclude_dow:
            continue
        if sym in open_syms or sym in pending_syms:
            continue
        if sym in recent_closed:
            continue

        cur_pos = len([p for p in positions if p.get("strategy") == "lsr_cross"])
        if cur_pos + len(pending) >= max_pos:
            break

        ts_unix = float(ts_val) if not isinstance(ts_val, (str, datetime)) else (
            ts_val.timestamp() if isinstance(ts_val, datetime) else
            datetime.fromisoformat(str(ts_val).replace("Z", "+00:00")).timestamp()
        )

        pending.append({
            "symbol": sym,
            "direction": s["direction"],
            "strategy": "lsr_cross",
            "ts": ts_unix,
            "ts_added": now.isoformat(),
            "z_score": s["z_score"],
        })
        pending_syms.add(sym)
        added += 1
        logger.info("[SIGNAL] %s %s z=%.2f", sym, s["direction"], s["z_score"])

    # Update pending + last_lsr_detect in strategy_config
    cur.execute(
        f"UPDATE {schema}.multi_state SET pending_signals=%s WHERE id=1",
        (json.dumps(pending),),
    )
    if not skip_cfg_write:
        # Load existing strategy_config, update last_lsr_detect, write back
        cur.execute(
            f"SELECT strategy_config FROM {schema}.multi_state WHERE id=1"
        )
        row = cur.fetchone()
        scfg = {}
        if row and row[0]:
            scfg = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
        scfg["last_lsr_detect"] = now.isoformat()
        cur.execute(
            f"UPDATE {schema}.multi_state SET strategy_config=%s WHERE id=1",
            (json.dumps(scfg),),
        )

    logger.info("[lsr_detect] added %d signals, pending=%d", added, len(pending))
    return added


# ─── Core: run_tick ──────────────────────────────────────────────────────

def run_tick(cur, cfg: dict, schema: str = "strategies",
             now_ts: datetime | None = None,
             bars_override: dict[str, list[tuple]] | None = None) -> dict:
    """Run one tick: open pending, scan/close open positions, MTM, persist.

    Returns summary dict {opened, closed, equity, trades: [...]}.
    Replicates live_tick.py run_tick exactly.

    ``now_ts`` overrides the wall-clock time (used by replay); ``bars_override``
    supplies the 1m bars directly instead of querying CH latest-window.
    """
    now = now_ts or datetime.now(timezone.utc)

    # ── Load state ──
    equity, peak, balance, positions = load_state(cur, schema)
    pending = load_pending(cur, schema)

    lsr_positions = [p for p in positions if p.get("strategy") == "lsr_cross"]
    lsr_syms = {p["symbol"] for p in lsr_positions}
    pending_lsr = [s for s in pending if s.get("strategy") == "lsr_cross"]
    all_syms = lsr_syms | {s["symbol"] for s in pending_lsr}

    if bars_override is not None:
        bars = bars_override
    else:
        bars = get_bars(list(all_syms))
    last_close = {sym: b[-1][3] for sym, b in bars.items() if b}

    # ── Config unpack ──
    sym_risk = cfg["sym_risk"]
    base_risk = cfg["base_risk"]
    max_pos = cfg["max_pos"]
    timeout_hours = cfg["timeout_bars"]
    lev = cfg["leverage"] or 1.0
    dd_stop_pct = cfg["dd_stop_pct"]
    margin_ratio = cfg["max_margin_ratio"]
    pyr_add = cfg["pyr_add"]

    closed_trades = []

    # ── 1. Pending → open ──
    opened = 0
    kept_pending = []
    for s in pending:
        if s.get("strategy") != "lsr_cross":
            kept_pending.append(s)
            continue
        sym = s["symbol"]
        px = last_close.get(sym)
        if not px:
            kept_pending.append(s)
            continue
        if sym in lsr_syms or len([p for p in positions if p.get("strategy") == "lsr_cross"]) >= max_pos:
            continue
        risk = base_risk * sym_risk.get(sym, 1.0)
        notional = equity * risk * lev
        margin = notional / lev
        total_margin = sum(
            p.get("margin", 0) for p in positions if p.get("strategy") == "lsr_cross"
        )
        if total_margin + margin > equity * margin_ratio:
            logger.warning("[MARGIN-LIMIT] %s: margin %.0f > %.0f",
                           sym, total_margin + margin, equity * margin_ratio)
            continue
        positions.append({
            "symbol": sym,
            "direction": s["direction"],
            "strategy": "lsr_cross",
            "entry_price": px,
            "risk": risk,
            "eq_open": equity,
            "contracts": notional / px if px > 0 else 0,
            "margin": margin,
            "ts_open": now.isoformat(),
            "ts_close": (now + timedelta(hours=timeout_hours)).isoformat(),
            "pyramid_added": False,
            "z_score": s.get("z_score", 0),
            "leverage": lev,
        })
        balance -= margin
        opened += 1
        logger.info("[OPEN] %s %s @ %.4f risk=%.0f%% z=%.2f",
                     sym, s["direction"], px, risk * 100, s.get("z_score", 0))

    cur.execute(
        f"UPDATE {schema}.multi_state SET pending_signals=%s WHERE id=1",
        (json.dumps(kept_pending),),
    )
    cur.connection.commit()

    # ── 2. Scan open positions ──
    remaining = []
    for p in positions:
        if p.get("strategy") != "lsr_cross":
            remaining.append(p)
            continue
        sym = p["symbol"]
        entry = p["entry_price"]
        d = 1 if p["direction"] == "LONG" else -1
        sym_bars = bars.get(sym)
        if not sym_bars:
            remaining.append(p)
            continue

        # Bound the scan window to bars at/after the position's entry time,
        # matching live (which scans from the entry window). Otherwise bars
        # BEFORE ts_open (in replay, from the start of the window) can trigger
        # false SL/TP/trail exits at stale prices.
        try:
            pos_open = _to_utc_naive(p["ts_open"])
        except Exception:
            pos_open = None
        if pos_open is not None:
            sym_bars = [b for b in sym_bars if b[0] >= pos_open]
        if not sym_bars:
            remaining.append(p)
            continue

        exit_px, reason, pyramid, trail_peak, trail_active = scan_position(
            entry, p["direction"], sym_bars, cfg,
            trail_peak=p.get("trail_peak"),
            trail_active=p.get("trail_active", False),
        )

        p["trail_peak"] = trail_peak
        p["trail_active"] = trail_active

        # Pyramid
        if pyramid and not p.get("pyramid_added"):
            add_margin = p["margin"] * pyr_add
            total_margin_p = sum(
                p2["margin"]
                for p2 in positions
                if p2.get("strategy") == "lsr_cross" and p2 is not p
            )
            if total_margin_p + p["margin"] + add_margin > equity * margin_ratio:
                logger.warning("[MARGIN-LIMIT] %s: pyr $%.0f exceeds limit $%.0f",
                               sym, add_margin, equity * margin_ratio)
                p["pyramid_added"] = True  # block re-check
                remaining.append(p)
                continue
            p["pyramid_added"] = True
            p["margin"] += add_margin
            p["contracts"] *= (1 + pyr_add)
            p["risk"] *= (1 + pyr_add)
            balance -= add_margin
            logger.info("[PYR] %s @ %.4f risk=%.0f%%",
                        sym, last_close.get(sym, entry), p["risk"] * 100)

        # Timeout
        if not reason:
            try:
                if now >= datetime.fromisoformat(p["ts_close"]):
                    px = last_close.get(sym)
                    if px:
                        exit_px, reason = px, "timeout"
            except Exception:
                pass

        # Close
        if reason:
            pnl_pct = (
                ((exit_px - entry) / entry if d == 1 else (entry - exit_px) / entry)
                - cfg["comm"] - cfg["slip"]
            )
            base_eq = float(p.get("eq_open", equity))
            pnl_usd = base_eq * p.get("risk", base_risk) * p.get("leverage", lev) * pnl_pct
            equity += pnl_usd
            if equity > peak:
                peak = equity
            balance += p.get("margin", 0)
            insert_closed_trade(
                cur, "lsr_cross", sym, p["direction"], pnl_usd, pnl_pct,
                p.get("ts_open"), now.isoformat(), entry, exit_px, reason, 0,
                schema=schema,
            )
            trade_info = {
                "symbol": sym, "direction": p["direction"],
                "reason": reason, "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 4), "entry": entry, "exit": exit_px,
            }
            closed_trades.append(trade_info)
            logger.info("[CLOSE] %s %s (%s) pnl=%+.2f$ eq=%.2f$",
                        sym, p["direction"], reason, pnl_usd, equity)
        else:
            remaining.append(p)

    # ── 3. MTM equity, peak, DD-stop ──
    lsr_left = [p for p in remaining if p.get("strategy") == "lsr_cross"]
    if lsr_left:
        mtm_dd_sum = 0.0
        for p in lsr_left:
            px = last_close.get(p["symbol"])
            if not px:
                continue
            entry = p["entry_price"]
            d = 1 if p["direction"] == "LONG" else -1
            mtm_dd = max(0, (entry - px) / entry if d == 1 else (px - entry) / entry)
            mtm_dd_sum += p.get("risk", base_risk) * p.get("leverage", lev) * mtm_dd
        eq_mtm = equity - equity * mtm_dd_sum
        if eq_mtm > peak:
            peak = eq_mtm
        dd = (peak - eq_mtm) / peak * 100 if peak > 0 else 0
        if dd > dd_stop_pct * 100:
            logger.warning("[DD-STOP] MTM DD %.1f%% > %.0f%% — closing all %d positions",
                           dd, dd_stop_pct * 100, len(lsr_left))
            for p in list(lsr_left):
                px = last_close.get(p["symbol"])
                if not px:
                    if p in remaining:
                        remaining.remove(p)
                    continue
                entry = p["entry_price"]
                d = 1 if p["direction"] == "LONG" else -1
                pnl_pct = (
                    ((px - entry) / entry if d == 1 else (entry - px) / entry)
                    - cfg["comm"] - cfg["slip"]
                )
                base_eq = float(p.get("eq_open", equity))
                pnl_usd = base_eq * p.get("risk", base_risk) * p.get("leverage", lev) * pnl_pct
                equity += pnl_usd
                if equity > peak:
                    peak = equity
                balance += p.get("margin", 0)
                insert_closed_trade(
                    cur, "lsr_cross", p["symbol"], p["direction"], pnl_usd, pnl_pct,
                    p.get("ts_open"), now.isoformat(), entry, px, "dd_stop", 0,
                    schema=schema,
                )
                trade_info = {
                    "symbol": p["symbol"], "direction": p["direction"],
                    "reason": "dd_stop", "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct, 4), "entry": entry, "exit": px,
                }
                closed_trades.append(trade_info)
                if p in remaining:
                    remaining.remove(p)
                logger.info("[CLOSE] %s %s (dd_stop) pnl=%+.2f$ eq=%.2f$",
                            p["symbol"], p["direction"], pnl_usd, equity)

    positions = remaining

    # ── Persist ──
    save_state(cur, equity, peak, balance, positions, schema=schema)
    cur.connection.commit()

    logger.info("[lsr_tick] positions=%d opened=%d closed=%d eq=%.2f",
                len(lsr_positions), opened, len(closed_trades), equity)

    return {
        "opened": opened,
        "closed": len(closed_trades),
        "equity": equity,
        "peak": peak,
        "balance": balance,
        "trades": closed_trades,
        "positions": len(positions),
    }


# ─── Scratch seeding ─────────────────────────────────────────────────────

def seed_scratch_state(cur, schema: str, equity: float = 475.0,
                       balance: float = 475.0, positions: list | None = None,
                       pending: list | None = None, strategy_config: dict | None = None):
    """Seed a scratch multi_state row with initial values."""
    cur.execute(
        f"INSERT INTO {schema}.multi_state "
        f"(id, equity, peak, balance, positions, pending_signals, strategy_config) "
        f"VALUES (1, %s, %s, %s, %s, %s, %s) "
        f"ON CONFLICT (id) DO UPDATE SET "
        f"equity=EXCLUDED.equity, peak=EXCLUDED.peak, balance=EXCLUDED.balance, "
        f"positions=EXCLUDED.positions, pending_signals=EXCLUDED.pending_signals, "
        f"strategy_config=EXCLUDED.strategy_config, updated_at=NOW()",
        (
            round(equity, 2), round(equity, 2), round(balance, 2),
            json.dumps(positions or []),
            json.dumps(pending or []),
            json.dumps(strategy_config or {}),
        ),
    )


# ─── Replay ──────────────────────────────────────────────────────────────

def load_full_klines(symbols: list[str], start: datetime, end: datetime,
                     ch_cfg: dict | None = None) -> dict[str, list[tuple]]:
    """Load full 1m bars (ts, hi, lo, cl) for symbols within [start, end]."""
    if not symbols:
        return {}
    ch_cfg = ch_cfg or DEFAULT_CH
    ch = clickhouse_connect.get_client(**ch_cfg)
    placeholders = ", ".join(["%s"] * len(symbols))
    q = (
        f"SELECT symbol, timestamp, high, low, close FROM klines_1m "
        f"WHERE symbol IN ({placeholders}) "
        f"AND timestamp >= toDateTime64('{start.isoformat()}', 3) "
        f"AND timestamp <= toDateTime64('{end.isoformat()}', 3) "
        f"ORDER BY timestamp"
    )
    rows = ch.query(q, parameters=symbols).result_rows
    ch.close()
    out: dict[str, list[tuple]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(
            (r[1], float(r[2]), float(r[3]), float(r[4]))
        )
    return out


def _to_utc_naive(dt) -> datetime:
    """Return a tz-aware UTC datetime (CH returns tz-aware Moscow; normalize).

    Accepts datetime, ISO string, or unix seconds.
    """
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(float(dt), tz=timezone.utc)
    elif isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def run_replay(cur, cfg: dict, schema: str,
               pending_events: list[dict], start: datetime, end: datetime,
               bars_override: dict[str, list[tuple]] | None = None,
               *, verbose: bool = True) -> dict:
    """Replay detect+tick forward over 1m bars, injecting pending signals.

    ``pending_events`` is a list of signal dicts, each with a ``ts`` (the
    timestamp at which it becomes due) plus symbol/direction/strategy/z_score.
    The signal is injected into pending at the first 1m step >= its ts.

    Uses time-travelling tick: at each 1m step ``now``, open due pending at
    the latest close <= now, and scan open positions over bars <= now.

    Returns summary {equity, peak, trades, steps, injected}.
    """
    start = _to_utc_naive(start)
    end = _to_utc_naive(end)

    all_symbols = set(cfg.get("symbols", []))
    for ev in pending_events:
        all_symbols.add(ev["symbol"])
    symbols = sorted(all_symbols)

    if bars_override is None:
        bars_override = load_full_klines(symbols, start, end)

    # Normalize bar timestamps to UTC
    for sym in bars_override:
        bars_override[sym] = [
            (_to_utc_naive(b[0]), b[1], b[2], b[3]) for b in bars_override[sym]
        ]

    # Build index of events by ts
    events = sorted(pending_events, key=lambda e: e["ts"])

    # Collect all unique timestamps across bars
    all_ts = set()
    for sym, bl in bars_override.items():
        for b in bl:
            all_ts.add(b[0])
    # Also ensure event timestamps and start/end are included
    timeline = sorted(
        ts for ts in all_ts
        if start <= ts <= end
    )

    injected = 0
    total_steps = 0
    closed_all = []

    # Load initial state
    equity, peak, balance, positions = load_state(cur, schema)

    # Persist initial (seed) state
    save_state(cur, equity, peak, balance, positions, schema=schema)
    cur.connection.commit()

    while events or timeline:
        # Determine next processing time
        next_event_ts = _to_utc_naive(events[0]["ts"]) if events else None
        next_bar_ts = timeline[0] if timeline else None
        # Advance timeline up to the next event (inject pending), or next bar
        if next_event_ts and (next_bar_ts is None or next_event_ts <= next_bar_ts):
            now = _to_utc_naive(next_event_ts) if isinstance(next_event_ts, datetime) else next_event_ts
        else:
            now = next_bar_ts

        # Inject all events due by 'now'
        due = [e for e in events if _to_utc_naive(e["ts"]) <= now]
        if due:
            eq, pk, bal, pos = load_state(cur, schema)
            pending_cur = load_pending(cur, schema)
            for ev in due:
                pending_cur.append({
                    "symbol": ev["symbol"],
                    "direction": ev["direction"],
                    "strategy": "lsr_cross",
                    "ts": ev["ts"],
                    "ts_added": now.isoformat(),
                    "z_score": ev.get("z_score", 0),
                })
                injected += 1
            save_state(cur, eq, pk, bal, pos, pending_cur, schema=schema)
            cur.connection.commit()
            # remove injected events
            events = [e for e in events if _to_utc_naive(e["ts"]) > now]

        # Advance timeline past 'now'
        while timeline and timeline[0] <= now:
            timeline.pop(0)

        # Build bars_override for this tick = all bars <= now (trailing window)
        sliced: dict[str, list[tuple]] = {}
        for sym, bl in bars_override.items():
            sliced[sym] = [b for b in bl if b[0] <= now]
        if not any(sliced.values()):
            continue

        # Run one tick at time 'now'
        res = run_tick(cur, cfg, schema=schema, now_ts=now, bars_override=sliced)
        total_steps += 1
        closed_all.extend(res["trades"])
        if verbose:
            for t in res["trades"]:
                print(f"  [{now:%m-%d %H:%M} UTC] CLOSE {t['symbol']} {t['direction']} "
                      f"({t['reason']}) pnl={t['pnl_usd']:+.2f}$ eq={res['equity']:,.2f}$")

    final_equity, final_peak, final_balance, final_pos = load_state(cur, schema)
    return {
        "equity": final_equity,
        "peak": final_peak,
        "balance": final_balance,
        "positions": len(final_pos),
        "steps": total_steps,
        "injected": injected,
        "trades": closed_all,
    }

