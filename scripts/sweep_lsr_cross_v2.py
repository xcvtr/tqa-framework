#!/usr/bin/env python3
"""LSR-CROSS v2 sweep — params + ATR-trail + EXCLUDE_HOURS/DOW sweep.

Usage:
    source ~/.hermes/hermes-agent/venv/bin/activate
    python scripts/sweep_lsr_cross_v2.py [--days 1095] [--risk 0.08] [--conc 6]
"""
from __future__ import annotations

import argparse, itertools, json, logging, sys, time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import requests as _req

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sweep_v2")

sys.path.insert(0, "/home/user/projects/tqa-framework")
from tqa_framework.engine.pg_state import PGState
from tqa_framework.engine.detect import load_m1_from_ch
from tqa_framework.engine.backtester import Backtester

CH_HOST = "http://10.0.0.60:8123"
CH_DB = "crypto"

TICKERS = [
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT",
]
EXCLUDE_SYMS = {"SOLUSDT"}
DEFAULT_EXCLUDE_HOURS = {11, 22, 23}
DEFAULT_EXCLUDE_DOW = {2}

SYM_RISK = {
    "ADAUSDT": 1.3, "ARBUSDT": 1.2, "OPUSDT": 1.1, "AVAXUSDT": 1.05,
    "DOGEUSDT": 1.0, "XRPUSDT": 1.0, "ETHUSDT": 1.0,
    "BNBUSDT": 1.0, "LINKUSDT": 1.0, "NEARUSDT": 0.9, "APTUSDT": 0.9,
}

def parse_ts(val) -> datetime:
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    return datetime.fromisoformat(str(val).replace("Z", "+00:00")).replace(tzinfo=None)

def get_end_time(ch_host: str, ch_db: str) -> str:
    q = f"SELECT max(timestamp) FROM {ch_db}.klines WHERE symbol='ETHUSDT' AND interval='5m' FORMAT TabSeparated"
    r = _req.get(ch_host, params={"query": q}, timeout=15)
    r.raise_for_status()
    return r.text.strip()

def load_lsr_data(symbol: str, hours: int, ch_host: str = CH_HOST, ch_db: str = CH_DB, end_time: str = ""):
    import requests as _r
    from statistics import mean, stdev
    lsr_query = f"""
    SELECT timestamp, ratio
    FROM crypto.long_short_ratio
    WHERE symbol='{symbol}' AND source='bybit_global'
    AND timestamp >= toDateTime64('{end_time}', 3, 'UTC') - INTERVAL {hours} HOUR
    AND timestamp <= toDateTime64('{end_time}', 3, 'UTC')
    ORDER BY timestamp
    FORMAT JSONEachRow
    """
    resp = _r.get(ch_host, params={"query": lsr_query}, timeout=30)
    resp.raise_for_status()
    raw = resp.text.strip()
    if not raw:
        return []
    lsr_data = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line: continue
        row = json.loads(line)
        lsr_data.append({'timestamp': row['timestamp'], 'ratio': row['ratio']})
    if not lsr_data:
        return []
    ratios = [row['ratio'] for row in lsr_data]
    window = 720
    zscores = []
    for i in range(len(ratios)):
        start = max(0, i - window + 1)
        if i - start + 1 >= 100:
            subset = ratios[start:i+1]
            mu = mean(subset)
            sigma = stdev(subset) if len(subset) > 1 else 0.001
            z = (ratios[i] - mu) / sigma if sigma > 0 else 0
            zscores.append(z)
        else:
            zscores.append(0.0)
    signals = []
    for i in range(1, len(zscores)):
        zprev, zcurr = zscores[i-1], zscores[i]
        if zprev < 2.0 and zcurr >= 2.0:
            direction = 'SHORT'
        elif zprev > -2.0 and zcurr <= -2.0:
            direction = 'LONG'
        else:
            continue
        evt = lsr_data[i]['timestamp']
        signals.append({
            'ts': evt, 'timestamp': evt, 'symbol': symbol, 'direction': direction,
            'zscore': zcurr, 'zprev': zprev, 'price_at_event': 0.0, 'action': 'signal'
        })
    return signals

# ── ATR calculation ──

def calc_atr(high: list, low: list, close: list, period: int = 14) -> np.ndarray:
    """Returns ATR values for each bar (ATR[0..period-1] = mean of first period TRs)."""
    high_a = np.array(high, dtype=float)
    low_a = np.array(low, dtype=float)
    close_a = np.array(close, dtype=float)
    tr = np.maximum(high_a - low_a, np.abs(high_a - np.roll(close_a, 1)))
    tr[0] = high_a[0] - low_a[0]
    atr = np.full_like(tr, np.nan)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr

# ── Preload ──

def preload(days: int, end_time: str):
    """Preload klines, LSR, and ATR."""
    logger.info(f"Preloading {len(TICKERS)} tickers, {days} days...")
    k5_cache: Dict[str, list] = {}
    k1_cache: Dict[str, list] = {}
    all_events: List[tuple] = []
    atr5_cache: Dict[str, np.ndarray] = {}

    for symbol in TICKERS:
        if symbol in EXCLUDE_SYMS:
            continue
        bars_5m = load_m1_from_ch(symbol, days * 24, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="5m")
        if not bars_5m or len(bars_5m) < 200:
            logger.warning(f"  {symbol}: мало 5m баров ({len(bars_5m or [])})")
            continue
        k5_cache[symbol] = bars_5m

        # ATR from 5m bars (open/high/low/close)
        atr5_cache[symbol] = calc_atr(
            [b["high"] for b in bars_5m],
            [b["low"] for b in bars_5m],
            [b["close"] for b in bars_5m],
            period=14,
        )

        bars_1m = load_m1_from_ch(symbol, days * 24, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="1m")
        if not bars_1m:
            bars_1m = bars_5m
        k1_cache[symbol] = bars_1m

        lsr_signals = load_lsr_data(symbol, hours=days * 24 + 720, ch_host=CH_HOST, ch_db=CH_DB, end_time=end_time)
        if not lsr_signals:
            continue
        for sig in lsr_signals:
            all_events.append((parse_ts(sig["ts"]), symbol, 1 if sig["direction"] == "LONG" else -1))
        logger.info(f"  {symbol}: {len(bars_5m)} 5m, {len(bars_1m)} 1m, "
                    f"{len([e for e in all_events if e[1] == symbol])} events")

    all_events.sort(key=lambda x: x[0])

    # Build numpy caches
    np5_cache = {}
    np1_cache = {}
    np_atr_cache = {}
    for sym in k5_cache:
        k5 = k5_cache[sym]
        np5_cache[sym] = (
            np.array([parse_ts(b["ts"]).timestamp() for b in k5]),
            np.array([b["high"] for b in k5], dtype=float),
            np.array([b["low"] for b in k5], dtype=float),
            np.array([b["close"] for b in k5], dtype=float),
        )
        if sym in atr5_cache:
            np_atr_cache[sym] = atr5_cache[sym]

    for sym in k1_cache:
        k1 = k1_cache[sym]
        np1_cache[sym] = (
            np.array([parse_ts(b["ts"]).timestamp() for b in k1]),
            np.array([b["high"] for b in k1], dtype=float),
            np.array([b["low"] for b in k1], dtype=float),
            np.array([b["close"] for b in k1], dtype=float),
        )

    price_map: Dict[str, OrderedDict] = {}
    for sym, bars in k1_cache.items():
        price_map[sym] = OrderedDict((parse_ts(b["ts"]), b["close"]) for b in bars)

    logger.info(f"Preloaded: {len(k5_cache)} symbols, {len(all_events)} events total")
    return k5_cache, k1_cache, np5_cache, np1_cache, np_atr_cache, price_map, all_events


# ── Enhanced simulate_trade with ATR-trail ──

def simulate_trade(symbol, event_ts, direction_int,
                   np5_cache, np1_cache, np_atr_cache,
                   sl_pct=0.05, tp_pct=0.18, hold_h=120,
                   pyr_trigger=0.05, pyr_add=0.5,
                   trail_act=0.02, trail_dist=0.015, trail_lock=0.0,
                   atr_trail=False, atr_period=14, atr_trail_act=2.0, atr_trail_dist=1.5,
                   atr_trail_lock=0.0):
    """Enhanced simulate_trade with optional ATR-trail."""
    if symbol not in np5_cache or symbol not in np1_cache:
        return None
    px_t5, px_h5, px_l5, px_c5 = np5_cache[symbol]
    px_t, px_h, px_l, px_c = np1_cache[symbol]

    ev_ts = parse_ts(event_ts).timestamp()
    ei = np.searchsorted(px_t5, ev_ts, side="right")
    if ei + 1 >= len(px_c5):
        return None
    entry = float(px_c5[ei])

    entry_ts = px_t5[ei]
    entry_close_ts = entry_ts + (px_t5[1] - px_t5[0])
    si = np.searchsorted(px_t, entry_close_ts, side="right")
    if si + 1 >= len(px_c):
        return None

    scan_mult = 60
    end_idx = min(si + int(hold_h * scan_mult), len(px_c) - 1)

    direction = direction_int
    sl_px = entry * (1 - sl_pct) if direction == 1 else entry * (1 + sl_pct)
    tp_px = entry * (1 + tp_pct) if direction == 1 else entry * (1 - tp_pct)
    pyr_px = entry * (1 + pyr_trigger) if direction == 1 else entry * (1 - pyr_trigger)
    exit_px = None
    exit_ret_ts = None
    mtm_worst = entry
    added = False
    base_risk = 1.0
    has_trail = (trail_act is not None and trail_act > 0) or atr_trail
    trail_active = False
    peak = entry

    for j in range(si, end_idx + 1):
        hi, lo = float(px_h[j]), float(px_l[j])
        if direction == 1:  # LONG
            mtm_worst = min(mtm_worst, lo)
            if not added and pyr_trigger > 0 and hi >= pyr_px:
                added = True
                base_risk += pyr_add
            if has_trail:
                if atr_trail:
                    # ATR-based trail: find closest 5m bar index
                    fi = max(0, np.searchsorted(px_t5, px_t[j], side="right") - 1)
                    fi = min(fi, len(np_atr_cache.get(symbol, [])) - 1)
                    atr_val = float(np_atr_cache[symbol][fi]) if symbol in np_atr_cache and not np.isnan(np_atr_cache[symbol][fi]) else 0
                    act_pct = (atr_val / entry) * atr_trail_act if atr_val > 0 and entry > 0 else trail_act
                    dist_pct = (atr_val / entry) * atr_trail_dist if atr_val > 0 and entry > 0 else trail_dist
                    lock_pct = (atr_val / entry) * atr_trail_lock if atr_val > 0 and entry > 0 else trail_lock
                else:
                    act_pct = trail_act
                    dist_pct = trail_dist
                    lock_pct = trail_lock

                if not trail_active and hi >= entry * (1 + act_pct):
                    trail_active = True
                    peak = hi
                    sl_px = entry
                if trail_active:
                    if hi > peak:
                        peak = hi
                    new_sl = float(peak * (1 - dist_pct))
                    if lock_pct > 0:
                        new_sl = max(new_sl, entry * (1 + lock_pct))
                    sl_px = max(sl_px, new_sl)
            if lo <= sl_px:
                exit_px = float(sl_px)
                exit_ret_ts = px_t[j]
                break
            if not trail_active and tp_pct > 0 and hi >= tp_px:
                exit_px = float(tp_px)
                exit_ret_ts = px_t[j]
                break
        else:  # SHORT
            mtm_worst = max(mtm_worst, hi)
            if not added and pyr_trigger > 0 and lo <= pyr_px:
                added = True
                base_risk += pyr_add
            if has_trail:
                if atr_trail:
                    fi = max(0, np.searchsorted(px_t5, px_t[j], side="right") - 1)
                    fi = min(fi, len(np_atr_cache.get(symbol, [])) - 1)
                    atr_val = float(np_atr_cache[symbol][fi]) if symbol in np_atr_cache and not np.isnan(np_atr_cache[symbol][fi]) else 0
                    act_pct = (atr_val / entry) * atr_trail_act if atr_val > 0 and entry > 0 else trail_act
                    dist_pct = (atr_val / entry) * atr_trail_dist if atr_val > 0 and entry > 0 else trail_dist
                    lock_pct = (atr_val / entry) * atr_trail_lock if atr_val > 0 and entry > 0 else trail_lock
                else:
                    act_pct = trail_act
                    dist_pct = trail_dist
                    lock_pct = trail_lock

                if not trail_active and lo <= entry * (1 - act_pct):
                    trail_active = True
                    peak = lo
                    sl_px = entry
                if trail_active:
                    if lo < peak:
                        peak = lo
                    new_sl = float(peak * (1 + dist_pct))
                    if lock_pct > 0:
                        new_sl = min(new_sl, entry * (1 - lock_pct))
                    sl_px = min(sl_px, new_sl)
            if hi >= sl_px:
                exit_px = float(sl_px)
                exit_ret_ts = px_t[j]
                break
            if not trail_active and tp_pct > 0 and lo <= tp_px:
                exit_px = float(tp_px)
                exit_ret_ts = px_t[j]
                break

    if exit_px is None:
        exit_px = float(px_c[end_idx])
        exit_ret_ts = datetime.utcfromtimestamp(px_t[end_idx])
    elif exit_ret_ts is not None:
        exit_ret_ts = datetime.utcfromtimestamp(exit_ret_ts)

    comm_slip = 0.0010 + 0.0005
    if direction == 1:
        pnl = (exit_px - entry) / entry - comm_slip
    else:
        pnl = (entry - exit_px) / entry - comm_slip

    return pnl * base_risk, mtm_worst, entry, exit_px, direction, base_risk, exit_ret_ts


# ── Sweep runner ──

def run_sweep(preloaded, risk_pct: float, max_conc: int,
              param_grid: Dict[str, list],
              initial_equity: float = 1000.0, leverage: float = 1.0,
              days: int = 1095) -> List[dict]:
    k5_cache, k1_cache, np5_cache, np1_cache, np_atr_cache, price_map, all_events = preloaded
    param_keys = list(param_grid.keys())
    param_values = list(param_grid.values())
    n_combos = len(list(itertools.product(*param_values)))
    logger.info(f"\nSweep: {n_combos} combos, {len(all_events)} events, risk={risk_pct}, conc={max_conc}")

    results = []
    for combo_vals in itertools.product(*param_values):
        combo = dict(zip(param_keys, combo_vals))
        sl_pct = combo.get("sl_pct", 0.05)
        tp_pct = combo.get("tp_pct", 0.18)
        hold_h = combo.get("hold_h", 120)
        pyr_trigger = combo.get("pyr_trigger", 0.05)
        pyr_add = combo.get("pyr_add", 0.5)
        trail_act = combo.get("trail_act", 0.02)
        trail_dist = combo.get("trail_dist", 0.015)
        trail_lock = combo.get("trail_lock", 0.0)
        atr_trail = combo.get("atr_trail", False)
        atr_period = combo.get("atr_period", 14)
        atr_trail_act = combo.get("atr_trail_act", 2.0)
        atr_trail_dist = combo.get("atr_trail_dist", 1.5)
        atr_trail_lock = combo.get("atr_trail_lock", 0.0)
        exclude_hours = combo.get("exclude_hours", None)
        exclude_dow = combo.get("exclude_dow", None)

        # Apply exclude hours/dow filter
        events_filtered = all_events
        if exclude_hours is not None:
            events_filtered = [e for e in events_filtered if e[0].hour not in exclude_hours]
        if exclude_dow is not None:
            events_filtered = [e for e in events_filtered if e[0].weekday() not in exclude_dow]

        dt0 = time.time()
        trade_results = []
        for event_dt, sym, d in events_filtered:
            if sym not in k5_cache or sym not in k1_cache:
                continue
            res = simulate_trade(sym, event_dt, d,
                                np5_cache, np1_cache, np_atr_cache,
                                sl_pct, tp_pct, hold_h,
                                pyr_trigger, pyr_add,
                                trail_act, trail_dist, trail_lock,
                                atr_trail, atr_period, atr_trail_act, atr_trail_dist, atr_trail_lock)
            if res is None:
                continue
            trade_results.append((event_dt, sym, d, res))

        if not trade_results:
            continue

        # Portfolio MTM
        eq = float(initial_equity)
        cash = eq
        peak = eq
        max_dd = 0.0
        positions = []
        for event_dt, sym, d, res in trade_results:
            pnl_eff, _, entry_px, exit_px, direction, base_risk, exit_ts = res
            positions.append({
                "entry_ts": event_dt, "symbol": sym, "direction": direction,
                "entry_px": entry_px, "exit_px": exit_px,
                "exit_ts_converted": exit_ts, "base_risk": base_risk,
                "pnl_eff": pnl_eff, "eq_at_entry": None, "opened": False,
            })

        positions.sort(key=lambda p: p["entry_ts"])
        all_ts = sorted(set().union(*[price_map[s].keys() for s in price_map if s in k5_cache]))
        next_pos_idx = 0
        active: list[dict] = []

        for bar_ts in all_ts:
            while next_pos_idx < len(positions):
                p = positions[next_pos_idx]
                if p["entry_ts"] <= bar_ts:
                    if len(active) < max_conc:
                        p["eq_at_entry"] = float(eq)
                        p["entry_equity"] = float(eq)
                        p["opened"] = True
                        active.append(p)
                    next_pos_idx += 1
                else:
                    break
            still_active = []
            for p in active:
                if p.get("eq_at_entry") is None:
                    continue
                exit_ts = p.get("exit_ts_converted", p["entry_ts"])
                if exit_ts < bar_ts:
                    r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                    pnl_dollars = p["eq_at_entry"] * r * leverage * p["pnl_eff"]
                    cash += pnl_dollars
                    p["eq_at_entry"] = None
                else:
                    still_active.append(p)
            active = still_active
            port_value = cash
            for p in active:
                if p.get("eq_at_entry") is None:
                    continue
                price = price_map[p["symbol"]].get(bar_ts)
                if price is not None:
                    r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                    if p["direction"] == 1:
                        mtm_pnl = (price - p["entry_px"]) / p["entry_px"]
                    else:
                        mtm_pnl = (p["entry_px"] - price) / p["entry_px"]
                    pos_value = p["eq_at_entry"] * r * leverage * mtm_pnl
                    port_value += pos_value
            eq = port_value
            if port_value > peak:
                peak = port_value
            dd = (peak - port_value) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        for p in positions:
            if p["opened"] and p.get("eq_at_entry") is not None:
                r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                pnl_dollars = p["eq_at_entry"] * r * leverage * p["pnl_eff"]
                cash += pnl_dollars

        total_return = (cash - initial_equity) / initial_equity * 100
        opened_trades = sum(1 for p in positions if p["opened"])
        results.append({
            "params": combo,
            "total_return": round(total_return, 2),
            "mdd": round(max_dd, 2),
            "cagr": round(total_return / (days / 365), 1),
            "trades": opened_trades,
            "calmar": round(total_return / max_dd, 2) if max_dd > 0 else 0,
            "time_sec": round(time.time() - dt0, 1),
        })

    dd_threshold = 15.0
    safe = [r for r in results if r["mdd"] <= dd_threshold]
    unsafe = [r for r in results if r["mdd"] > dd_threshold]
    safe.sort(key=lambda r: r["cagr"], reverse=True)
    unsafe.sort(key=lambda r: r["cagr"], reverse=True)
    return safe + unsafe


def print_results(results: List[dict], label: str):
    logger.info(f"\n{'='*70}")
    logger.info(f"{label}")
    logger.info(f"{'='*70}")
    safe = [r for r in results if r["mdd"] <= 15.0]
    logger.info(f"{'#':>3}  {'CAGR':>6}  {'DD':>6}  {'Ret%':>8}  {'Calmar':>6}  {'Trd':>4}  {'Params'}")
    logger.info("-"*70)
    for i, r in enumerate(results[:15]):
        p = r["params"]
        tag = " ✓SAFE" if r["mdd"] <= 15.0 else ""
        logger.info(f"{i+1:>3}  {r['cagr']:>5.0f}%  {r['mdd']:>5.1f}%  {r['total_return']:>7.2f}%  "
                    f"{r['calmar']:>5.1f}  {r['trades']:>4d}  {p}{tag}")
    if safe:
        best = safe[0]
        logger.info(f"\n  BEST SAFE (DD≤15%): CAGR={best['cagr']:.0f}%  DD={best['mdd']:.1f}%  "
                    f"Ret={best['total_return']:.1f}%  Calmar={best['calmar']:.1f}  {best['params']}")
    logger.info("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--risk", type=float, default=0.08)
    parser.add_argument("--conc", type=int, default=6)
    parser.add_argument("--skip-phases", type=str, default="", help="Comma-separated phase numbers to skip")
    args = parser.parse_args()

    days = args.days
    risk_pct = args.risk
    max_conc = args.conc
    skip = set(args.skip_phases.split(",")) if args.skip_phases else set()

    end_time = get_end_time(CH_HOST, CH_DB)
    logger.info(f"End time: {end_time}, days={days}, risk={risk_pct}, conc={max_conc}")

    data = preload(days, end_time)

    # ──────────────────────────────────────────────────
    # PHASE 1: BASELINE — existing default params
    # ──────────────────────────────────────────────────
    if "1" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05], "tp_pct": [0.18],
            "pyr_trigger": [0.05], "pyr_add": [0.5],
            "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
            "hold_h": [120],
            "atr_trail": [False],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 1: BASELINE (default params)")

    # ──────────────────────────────────────────────────
    # PHASE 2: SWEEP SL_PCT + TP_PCT (no trail, no pyr)
    # ──────────────────────────────────────────────────
    if "2" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.02, 0.03, 0.04, 0.05, 0.06, 0.07],
            "tp_pct": [0.08, 0.10, 0.15, 0.18, 0.25, 0.35],
            "pyr_trigger": [0], "pyr_add": [0],
            "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
            "hold_h": [120],
            "atr_trail": [False],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 2: SWEEP SL/TP (no trail, no pyr)")

    # ──────────────────────────────────────────────────
    # PHASE 3: SWEEP FIXED TRAIL (baseline exit)
    # ──────────────────────────────────────────────────
    if "3" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05],
            "tp_pct": [0.18],
            "pyr_trigger": [0.05], "pyr_add": [0.5],
            "trail_act": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
            "trail_dist": [0.005, 0.008, 0.010, 0.015, 0.020, 0.030],
            "trail_lock": [0.0, 0.01, 0.02],
            "hold_h": [120],
            "atr_trail": [False],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 3: SWEEP FIXED TRAIL (6×6×3=108 combos)")

    # ──────────────────────────────────────────────────
    # PHASE 4: SWEEP PURE TRAIL (no fixed TP)
    # ──────────────────────────────────────────────────
    if "4" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05],
            "tp_pct": [0],
            "pyr_trigger": [0.05], "pyr_add": [0.5],
            "trail_act": [0.01, 0.02, 0.03, 0.04],
            "trail_dist": [0.005, 0.008, 0.010, 0.015, 0.020],
            "trail_lock": [0.0, 0.02],
            "hold_h": [168],
            "atr_trail": [False],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 4: SWEEP PURE TRAIL (no TP)")

    # ──────────────────────────────────────────────────
    # PHASE 5: SWEEP ATR-TRAIL (ATR multiples)
    # ──────────────────────────────────────────────────
    if "5" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05],
            "tp_pct": [0.18],
            "pyr_trigger": [0.05], "pyr_add": [0.5],
            "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
            "hold_h": [120],
            "atr_trail": [True],
            "atr_period": [14],
            "atr_trail_act": [1.0, 1.5, 2.0, 3.0, 4.0],
            "atr_trail_dist": [0.5, 1.0, 1.5, 2.0, 3.0],
            "atr_trail_lock": [0.0, 0.5, 1.0],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 5: SWEEP ATR-TRAIL (5×5×3=75 combos)")

    # ──────────────────────────────────────────────────
    # PHASE 6: SWEEP PURE ATR-TRAIL (no TP, longer hold)
    # ──────────────────────────────────────────────────
    if "6" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05],
            "tp_pct": [0],
            "pyr_trigger": [0.05], "pyr_add": [0.5],
            "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
            "hold_h": [168],
            "atr_trail": [True],
            "atr_period": [14],
            "atr_trail_act": [1.0, 2.0, 3.0, 4.0],
            "atr_trail_dist": [0.5, 1.0, 1.5, 2.0, 3.0],
            "atr_trail_lock": [0.0, 1.0],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 6: SWEEP PURE ATR-TRAIL (no TP)")

    # ──────────────────────────────────────────────────
    # PHASE 7: SWEEP PYRAMIDING (best exit params)
    # ──────────────────────────────────────────────────
    if "7" not in skip:
        r = run_sweep(data, risk_pct, max_conc, {
            "sl_pct": [0.05],
            "tp_pct": [0.18],
            "pyr_trigger": [0.02, 0.03, 0.05, 0.08, 0.10],
            "pyr_add": [0.3, 0.5, 0.7, 1.0],
            "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
            "hold_h": [120],
            "atr_trail": [False],
            "exclude_hours": [None], "exclude_dow": [None],
        }, days=days)
        print_results(r, "PHASE 7: SWEEP PYRAMIDING (5×4=20 combos)")

    # ──────────────────────────────────────────────────
    # PHASE 8: SWEEP EXCLUDE_HOURS (best params)
    # ──────────────────────────────────────────────────
    if "8" not in skip:
        hour_sets = [
            None,          # no exclusion
            frozenset({22, 23}),   # late night only
            frozenset({11, 22, 23}),  # original
            frozenset({1, 2, 22, 23}),  # + early morning
            frozenset({20, 21, 22, 23}),  # evening
            frozenset({0, 1, 2, 3, 22, 23}),  # wide
        ]
        for hs in hour_sets:
            r = run_sweep(data, risk_pct, max_conc, {
                "sl_pct": [0.05], "tp_pct": [0.18],
                "pyr_trigger": [0.05], "pyr_add": [0.5],
                "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
                "hold_h": [120],
                "atr_trail": [False],
                "exclude_hours": [hs],
                "exclude_dow": [None],
            }, days=days)
            label = f"PHASE 8a: EXCL_HOURS {set(hs) if hs else 'NONE'}"
            print_results(r, label)

    # ──────────────────────────────────────────────────
    # PHASE 9: SWEEP EXCLUDE_DOW (best params)
    # ──────────────────────────────────────────────────
    if "9" not in skip:
        dow_sets = [
            None,       # no exclusion
            frozenset({2}),  # Wednesday (original)
            frozenset({1, 2}),  # Tue-Wed
            frozenset({0, 4}),  # Mon+Fri
            frozenset({5, 6}),  # weekend
        ]
        for ds in dow_sets:
            r = run_sweep(data, risk_pct, max_conc, {
                "sl_pct": [0.05], "tp_pct": [0.18],
                "pyr_trigger": [0.05], "pyr_add": [0.5],
                "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
                "hold_h": [120],
                "atr_trail": [False],
                "exclude_hours": [None],
                "exclude_dow": [ds],
            }, days=days)
            label = f"PHASE 9a: EXCL_DOW {set(ds) if ds else 'NONE'}"
            print_results(r, label)

    # ──────────────────────────────────────────────────
    # MASTER: BEST OF ALL
    # ──────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("MASTER RESULTS — BEST CONFIGS ACROSS ALL PHASES")
    logger.info("=" * 70)