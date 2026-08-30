#!/usr/bin/env python3
"""LSR-CROSS v3 sweep — fast per-event scoring + final MTM verification.

Strategy:
  1. Preload data once (11 tickers, 1095 days)
  2. For each param combo: run simulate_trade (fast), score per-trade metrics
  3. Estimate DD from max concurrent adverse move (faster than full MTM loop)
  4. Top N configs → full portfolio MTM for accurate DD
  5. Report best
"""
from __future__ import annotations

import argparse, itertools, json, logging, sys, time, multiprocessing as mp
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Tuple
import math

import numpy as np
import requests as _req

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sweep_v3")

sys.path.insert(0, "/home/user/projects/tqa-framework")
from tqa_framework.engine.detect import load_m1_from_ch

CH_HOST = "http://10.0.0.60:8123"
CH_DB = "crypto"

TICKERS = [
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT",
]
EXCLUDE_SYMS = {"SOLUSDT"}

SYM_RISK = {
    "ADAUSDT": 1.3, "ARBUSDT": 1.2, "OPUSDT": 1.1, "AVAXUSDT": 1.05,
    "DOGEUSDT": 1.0, "XRPUSDT": 1.0, "ETHUSDT": 1.0,
    "BNBUSDT": 1.0, "LINKUSDT": 1.0, "NEARUSDT": 0.9, "APTUSDT": 0.9,
}

def parse_ts(val) -> datetime:
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    return datetime.fromisoformat(str(val).replace("Z", "+00:00")).replace(tzinfo=None)

def get_end_time() -> str:
    q = f"SELECT max(timestamp) FROM {CH_DB}.klines WHERE symbol='ETHUSDT' AND interval='5m' FORMAT TabSeparated"
    r = _req.get(CH_HOST, params={"query": q}, timeout=15)
    r.raise_for_status()
    return r.text.strip()

# ── ATR ──
def calc_atr(high, low, close, period=14):
    ha = np.array(high, dtype=float)
    la = np.array(low, dtype=float)
    ca = np.array(close, dtype=float)
    tr = np.maximum(ha - la, np.abs(ha - np.roll(ca, 1)))
    tr[0] = ha[0] - la[0]
    atr = np.full_like(tr, np.nan)
    atr[period-1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr

# ── Preload ──
def preload(days: int, end_time: str):
    logger.info(f"Preloading {len(TICKERS)} tickers, {days} days...")
    np5, np1, atr5 = {}, {}, {}
    price_map = {}
    all_events: List[tuple] = []

    for symbol in TICKERS:
        if symbol in EXCLUDE_SYMS:
            continue
        b5 = load_m1_from_ch(symbol, days*24+720, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="5m")
        if not b5 or len(b5) < 200:
            logger.warning(f"  {symbol}: мало 5m ({len(b5 or [])})")
            continue
        ts5 = np.array([parse_ts(b['ts']).timestamp() for b in b5])
        np5[symbol] = (
            ts5,
            np.array([b['high'] for b in b5], dtype=float),
            np.array([b['low'] for b in b5], dtype=float),
            np.array([b['close'] for b in b5], dtype=float),
        )
        atr5[symbol] = calc_atr([b['high'] for b in b5], [b['low'] for b in b5], [b['close'] for b in b5])

        b1 = load_m1_from_ch(symbol, days*24+720, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="1m")
        if not b1:
            b1 = b5
        np1[symbol] = (
            np.array([parse_ts(b['ts']).timestamp() for b in b1]),
            np.array([b['high'] for b in b1], dtype=float),
            np.array([b['low'] for b in b1], dtype=float),
            np.array([b['close'] for b in b1], dtype=float),
        )

        # LSR
        lsr_q = f"SELECT timestamp, ratio FROM {CH_DB}.long_short_ratio WHERE symbol='{symbol}' AND source='bybit_global' AND timestamp >= toDateTime64('{end_time}',3,'UTC') - INTERVAL {days*24+720} HOUR AND timestamp <= toDateTime64('{end_time}',3,'UTC') ORDER BY timestamp FORMAT JSONEachRow"
        r = _req.get(CH_HOST, params={"query": lsr_q}, timeout=30)
        raw = r.text.strip()
        if not raw:
            continue
        lsr_data = [json.loads(l) for l in raw.split('\n') if l.strip()]
        ratios = [row['ratio'] for row in lsr_data]
        # z-score
        zs = np.zeros(len(ratios))
        for i in range(100, len(ratios)):
            mu = np.mean(ratios[i-720:i+1]) if i >= 720 else np.mean(ratios[:i+1])
            sigma = np.std(ratios[max(0,i-720):i+1]) if i >= 100 else 1
            zs[i] = (ratios[i] - mu) / max(sigma, 0.001)
        # cross detection
        evts = []
        for i in range(1, len(zs)):
            if zs[i-1] < 2.0 <= zs[i]:
                evts.append((lsr_data[i]['timestamp'], symbol, -1))
            elif zs[i-1] > -2.0 >= zs[i]:
                evts.append((lsr_data[i]['timestamp'], symbol, 1))
        for e in evts:
            all_events.append((parse_ts(e[0]), e[1], e[2]))

        # price map for MTM
        pm = OrderedDict()
        for b in b1:
            pm[parse_ts(b['ts'])] = b['close']
        price_map[symbol] = pm

        logger.info(f"  {symbol}: {len(b5)} 5m, {len(b1)} 1m, {len(evts)} events")

    all_events.sort(key=lambda x: x[0])
    logger.info(f"Preloaded: {len(np5)} symbols, {len(all_events)} events total")
    return np5, np1, atr5, price_map, all_events


def simulate_trade(symbol, event_ts, direction_int, np5, np1, atr5,
                   sl_pct=0.05, tp_pct=0.18, hold_h=120,
                   pyr_trigger=0.05, pyr_add=0.5,
                   trail_act=0.02, trail_dist=0.015, trail_lock=0.0,
                   atr_trail=False, atr_trail_act=2.0, atr_trail_dist=1.5, atr_trail_lock=0.0):
    """Fast simulate_trade with numpy. Returns (pnl_eff, entry_px, exit_px, dir, base_risk, exit_ts)."""
    px_t5, px_h5, px_l5, px_c5 = np5[symbol]
    px_t, px_h, px_l, px_c = np1[symbol]

    ev_ts = parse_ts(event_ts).timestamp()
    ei = np.searchsorted(px_t5, ev_ts, side="right")
    if ei + 1 >= len(px_c5):
        return None
    entry = float(px_c5[ei])

    entry_close_ts = px_t5[ei] + (px_t5[1] - px_t5[0])
    si = np.searchsorted(px_t, entry_close_ts, side="right")
    if si + 1 >= len(px_c):
        return None

    end_idx = min(si + int(hold_h * 60), len(px_c) - 1)
    direction = direction_int

    sl_px = entry * (1 - sl_pct) if direction == 1 else entry * (1 + sl_pct)
    tp_px = entry * (1 + tp_pct) if direction == 1 else entry * (1 - tp_pct)
    pyr_px = entry * (1 + pyr_trigger) if direction == 1 else entry * (1 - pyr_trigger)
    exit_px = None
    exit_ret_ts = None
    added = False
    base_risk = 1.0
    trail_active = False
    peak = entry

    for j in range(si, end_idx + 1):
        hi, lo = float(px_h[j]), float(px_l[j])
        if direction == 1:
            if not added and pyr_trigger > 0 and hi >= pyr_px:
                added = True
                base_risk += pyr_add
            if atr_trail or trail_act > 0:
                if atr_trail:
                    fi = max(0, np.searchsorted(px_t5, px_t[j], side="right") - 1)
                    fi = min(fi, len(atr5[symbol]) - 1)
                    av = float(atr5[symbol][fi]) if symbol in atr5 and not np.isnan(atr5[symbol][fi]) else 0
                    act_pct = (av / entry) * atr_trail_act if av > 0 else trail_act
                    dist_pct = (av / entry) * atr_trail_dist if av > 0 else trail_dist
                    lk_pct = (av / entry) * atr_trail_lock if av > 0 else trail_lock
                else:
                    act_pct, dist_pct, lk_pct = trail_act, trail_dist, trail_lock
                if not trail_active and hi >= entry * (1 + act_pct):
                    trail_active = True
                    peak = hi
                    sl_px = entry
                if trail_active:
                    if hi > peak: peak = hi
                    ns = float(peak * (1 - dist_pct))
                    if lk_pct > 0: ns = max(ns, entry * (1 + lk_pct))
                    sl_px = max(sl_px, ns)
            if lo <= sl_px:
                exit_px, exit_ret_ts = float(sl_px), px_t[j]
                break
            if not trail_active and tp_pct > 0 and hi >= tp_px:
                exit_px, exit_ret_ts = float(tp_px), px_t[j]
                break
        else:
            if not added and pyr_trigger > 0 and lo <= pyr_px:
                added = True
                base_risk += pyr_add
            if atr_trail or trail_act > 0:
                if atr_trail:
                    fi = max(0, np.searchsorted(px_t5, px_t[j], side="right") - 1)
                    fi = min(fi, len(atr5[symbol]) - 1)
                    av = float(atr5[symbol][fi]) if symbol in atr5 and not np.isnan(atr5[symbol][fi]) else 0
                    act_pct = (av / entry) * atr_trail_act if av > 0 else trail_act
                    dist_pct = (av / entry) * atr_trail_dist if av > 0 else trail_dist
                    lk_pct = (av / entry) * atr_trail_lock if av > 0 else trail_lock
                else:
                    act_pct, dist_pct, lk_pct = trail_act, trail_dist, trail_lock
                if not trail_active and lo <= entry * (1 - act_pct):
                    trail_active = True
                    peak = lo
                    sl_px = entry
                if trail_active:
                    if lo < peak: peak = lo
                    ns = float(peak * (1 + dist_pct))
                    if lk_pct > 0: ns = min(ns, entry * (1 - lk_pct))
                    sl_px = min(sl_px, ns)
            if hi >= sl_px:
                exit_px, exit_ret_ts = float(sl_px), px_t[j]
                break
            if not trail_active and tp_pct > 0 and lo <= tp_px:
                exit_px, exit_ret_ts = float(tp_px), px_t[j]
                break

    if exit_px is None:
        exit_px, exit_ret_ts = float(px_c[end_idx]), px_t[end_idx]
    elif exit_ret_ts is not None:
        exit_ret_ts = exit_ret_ts

    cs = 0.0015  # comm + slip
    if direction == 1:
        pnl = (exit_px - entry) / entry - cs
    else:
        pnl = (entry - exit_px) / entry - cs
    return pnl * base_risk


# ── Fast sweep per combo (no portfolio MTM) ──

def score_combo(combo, all_events, np5, np1, atr5, price_map, risk_pct, max_conc, days, initial_equity=1000):
    """Score one param combo with timeline-based portfolio MTM (event boundaries only)."""
    t0 = time.time()
    sl_pct = combo.get("sl_pct", 0.05)
    tp_pct = combo.get("tp_pct", 0.18)
    hold_h = combo.get("hold_h", 120)
    pyr_trigger = combo.get("pyr_trigger", 0.05)
    pyr_add = combo.get("pyr_add", 0.5)
    trail_act = combo.get("trail_act", 0.02)
    trail_dist = combo.get("trail_dist", 0.015)
    trail_lock = combo.get("trail_lock", 0.0)
    atr_trail = combo.get("atr_trail", False)
    atr_trail_act = combo.get("atr_trail_act", 2.0)
    atr_trail_dist = combo.get("atr_trail_dist", 1.5)
    atr_trail_lock = combo.get("atr_trail_lock", 0.0)
    exclude_hours = combo.get("exclude_hours", None)
    exclude_dow = combo.get("exclude_dow", None)

    events = all_events
    if exclude_hours is not None:
        events = [e for e in events if e[0].hour not in exclude_hours]
    if exclude_dow is not None:
        events = [e for e in events if e[0].weekday() not in exclude_dow]

    # Pre-compute trades
    px_t5_cache = {sym: np5[sym][0] for sym in np5}
    px_c5_cache = {sym: np5[sym][3] for sym in np5}

    wr_count = 0
    positions_list = []
    for event_dt, sym, d in events:
        if sym not in np5 or sym not in np1:
            continue
        ev_ts = parse_ts(event_dt).timestamp()
        ei = np.searchsorted(px_t5_cache[sym], ev_ts, side="right")
        if ei + 1 >= len(px_c5_cache[sym]):
            continue
        entry_px = float(px_c5_cache[sym][ei])
        pnl = simulate_trade(sym, event_dt, d, np5, np1, atr5,
                            sl_pct, tp_pct, hold_h, pyr_trigger, pyr_add,
                            trail_act, trail_dist, trail_lock,
                            atr_trail, atr_trail_act, atr_trail_dist, atr_trail_lock)
        if pnl is None:
            continue
        if pnl > 0:
            wr_count += 1
        exit_dt = datetime.fromtimestamp(parse_ts(event_dt).timestamp() + hold_h * 3600)
        positions_list.append((event_dt, exit_dt, sym, d, entry_px, pnl))

    if not positions_list:
        return None

    # ── Timeline MTM ──
    timeline = sorted(set().union(*[(p[0], p[1]) for p in positions_list]))
    eq = initial_equity
    cash = eq
    peak = eq
    max_dd = 0.0
    next_idx = 0
    active = {}
    price_map_syms = {s: price_map.get(s, {}) for s in set(p[2] for p in positions_list)}

    for bar_ts in timeline:
        while next_idx < len(positions_list):
            entry_dt, exit_dt, sym, d, entry_px, pnl_eff = positions_list[next_idx]
            if entry_dt <= bar_ts:
                if len(active) < max_conc:
                    active[next_idx] = {
                        "exit_ts": exit_dt, "symbol": sym,
                        "direction": d, "entry_px": entry_px,
                        "pnl_eff": pnl_eff, "eq_at_entry": eq,
                    }
                next_idx += 1
            else:
                break

        still_active = {}
        for idx, p in active.items():
            if p["exit_ts"] <= bar_ts:
                r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                cash += p["eq_at_entry"] * r * p["pnl_eff"]
            else:
                still_active[idx] = p
        active = still_active

        port_value = cash
        for p in active.values():
            price = price_map_syms[p["symbol"]].get(bar_ts)
            if price is not None:
                r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                mtm = (price - p["entry_px"]) / p["entry_px"] if p["direction"] == 1 else (p["entry_px"] - price) / p["entry_px"]
                port_value += p["eq_at_entry"] * r * mtm
        eq = port_value
        if port_value > peak:
            peak = port_value
        dd = (peak - port_value) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    for p in active.values():
        r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
        cash += p["eq_at_entry"] * r * p["pnl_eff"]

    total_ret = (cash - initial_equity) / initial_equity * 100
    cagr = total_ret / (days / 365)
    wr = wr_count / len(positions_list) * 100 if positions_list else 0

    return {
        "cagr": round(cagr, 1),
        "mdd_est": round(max_dd, 2),
        "total_return": round(total_ret, 2),
        "trades": len(positions_list),
        "wr": round(wr, 1),
        "calmar_est": round(cagr / max(max_dd, 0.1), 1),
        "time_sec": round(time.time() - t0, 1),
        "params": combo,
    }


def run_verification(combo, all_events, np5, np1, atr5, price_map, risk_pct, max_conc, days, initial_equity=1000):
    """Full portfolio MTM — optimized: only iterates event timestamps, not all 1m bars."""
    t0 = time.time()
    sl_pct = combo.get("sl_pct", 0.05)
    tp_pct = combo.get("tp_pct", 0.18)
    hold_h = combo.get("hold_h", 120)
    pyr_trigger = combo.get("pyr_trigger", 0.05)
    pyr_add = combo.get("pyr_add", 0.5)
    trail_act = combo.get("trail_act", 0.02)
    trail_dist = combo.get("trail_dist", 0.015)
    trail_lock = combo.get("trail_lock", 0.0)
    atr_trail = combo.get("atr_trail", False)
    atr_trail_act = combo.get("atr_trail_act", 2.0)
    atr_trail_dist = combo.get("atr_trail_dist", 1.5)
    atr_trail_lock = combo.get("atr_trail_lock", 0.0)
    exclude_hours = combo.get("exclude_hours", None)
    exclude_dow = combo.get("exclude_dow", None)

    events = all_events
    if exclude_hours is not None:
        events = [e for e in events if e[0].hour not in exclude_hours]
    if exclude_dow is not None:
        events = [e for e in events if e[0].weekday() not in exclude_dow]

    # Pre-compute all trades: pnl_eff, entry_px, exit_ts
    # Need simulate_trade to return more info. Use a modified call.
    px_t5, px_h5, px_l5, px_c5 = {}, {}, {}, {}
    for sym in np5:
        px_t5[sym], px_h5[sym], px_l5[sym], px_c5[sym] = np5[sym]
    
    # We need entry_px and exit_ts for MTM. Let's compute them in a fast batch.
    # Simulate trades collecting both entry_px and exit_ts
    from collections import namedtuple
    
    positions_list = []
    for event_dt, sym, d in events:
        if sym not in np5 or sym not in np1:
            continue
        ev_ts = parse_ts(event_dt).timestamp()
        ei = np.searchsorted(px_t5[sym], ev_ts, side="right")
        if ei + 1 >= len(px_c5[sym]):
            continue
        entry_px = float(px_c5[sym][ei])
        
        # Quick simulate for exit
        res = simulate_trade(sym, event_dt, d, np5, np1, atr5,
                            sl_pct, tp_pct, hold_h, pyr_trigger, pyr_add,
                            trail_act, trail_dist, trail_lock,
                            atr_trail, atr_trail_act, atr_trail_dist, atr_trail_lock)
        if res is None:
            continue
        pnl_eff = res
        
        # Estimate exit as entry + hold_h (simplified)
        exit_ts_dt = datetime.fromtimestamp(parse_ts(event_dt).timestamp() + hold_h * 3600)
        positions_list.append((event_dt, exit_ts_dt, sym, d, entry_px, pnl_eff))
    
    if not positions_list:
        return None
    
    # Sort by entry time
    positions_list.sort(key=lambda x: x[0])
    
    # Build timeline: all entry and exit timestamps
    timeline = set()
    for entry_dt, exit_dt, _, _, _, _ in positions_list:
        timeline.add(entry_dt)
        timeline.add(exit_dt)
    timeline = sorted(timeline)
    
    eq = initial_equity
    cash = eq
    peak = eq
    max_dd = 0.0
    next_idx = 0
    active = {}
    price_map_syms = {s: price_map.get(s, {}) for s in set(p[2] for p in positions_list)}
    
    for bar_ts in timeline:
        # Open
        while next_idx < len(positions_list):
            entry_dt, exit_dt, sym, d, entry_px, pnl_eff = positions_list[next_idx]
            if entry_dt <= bar_ts:
                if len(active) < max_conc:
                    active[next_idx] = {
                        "entry_ts": entry_dt, "exit_ts": exit_dt,
                        "symbol": sym, "direction": d, "entry_px": entry_px,
                        "pnl_eff": pnl_eff, "eq_at_entry": eq,
                    }
                next_idx += 1
            else:
                break
        
        # Close
        still_active = {}
        for idx, p in active.items():
            if p["exit_ts"] <= bar_ts:
                r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                cash += p["eq_at_entry"] * r * p["pnl_eff"]
            else:
                still_active[idx] = p
        active = still_active
        
        # MTM
        port_value = cash
        for p in active.values():
            price = price_map_syms[p["symbol"]].get(bar_ts)
            if price is not None:
                r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
                mtm = (price - p["entry_px"]) / p["entry_px"] if p["direction"] == 1 else (p["entry_px"] - price) / p["entry_px"]
                port_value += p["eq_at_entry"] * r * mtm
        
        eq = port_value
        if port_value > peak:
            peak = port_value
        dd = (peak - port_value) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    
    # Close remaining
    for p in active.values():
        r = risk_pct * SYM_RISK.get(p["symbol"], 1.0)
        cash += p["eq_at_entry"] * r * p["pnl_eff"]
    
    total_ret = (cash - initial_equity) / initial_equity * 100
    cagr = total_ret / (days / 365)
    
    return {
        "params": combo,
        "total_return": round(total_ret, 2),
        "mdd": round(max_dd, 2),
        "cagr": round(cagr, 1),
        "trades": len(positions_list),
        "calmar": round(total_ret / max_dd, 2) if max_dd > 0 else 0,
        "time_sec": round(time.time() - t0, 1),
    }


def print_results(results, label):
    safe = [r for r in results if r.get("mdd_est", r.get("mdd", 999)) <= 15.0]
    logger.info(f"\n{'='*70}")
    logger.info(f"{label}  ({'safe' if len(results)<=10 else f'{len(safe)} safe'} of {len(results)})")
    logger.info(f"{'='*70}")
    logger.info(f"{'#':>3}  {'CAGR':>6}  {'DD':>6}  {'Ret%':>8}  {'Calmar':>5}  {'Trd':>4}  {'WR%':>5}  {'PF':>5}  {'Params'}")
    logger.info("-"*70)
    for i, r in enumerate(results[:15]):
        p = r["params"]
        dd_val = r.get("mdd_est", r.get("mdd", 99))
        tag = " ✓" if dd_val <= 15.0 else ""
        c = r.get("calmar_est", r.get("calmar", 0))
        logger.info(f"{i+1:>3}  {r['cagr']:>5.0f}%  {dd_val:>5.1f}%  {r['total_return']:>7.2f}%  "
                    f"{c:>5.1f}  {r['trades']:>4d}  {r.get('wr',0):>4.1f}  {r.get('pf',0):>4.2f}  {p}{tag}")
    if safe:
        b = safe[0]
        dd_v = b.get("mdd_est", b.get("mdd", 99))
        logger.info(f"\n  BEST SAFE: CAGR={b['cagr']:.0f}%  DD={dd_v:.1f}%  Ret={b['total_return']:.1f}%  {b['params']}")
    logger.info("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--risk", type=float, default=0.08)
    parser.add_argument("--conc", type=int, default=6)
    parser.add_argument("--verify-top", type=int, default=5, help="Verify top N with full MTM")
    args = parser.parse_args()

    days = args.days; risk_pct = args.risk; max_conc = args.conc
    end_time = get_end_time()
    logger.info(f"End time: {end_time}, days={days}, risk={risk_pct}, conc={max_conc}")

    data = preload(days, end_time)
    np5, np1, atr5, price_map, all_events = data

    # ── Phase definitions ──
    phases = []

    # P1: Baseline
    phases.append(("P1: BASELINE", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P2: SL/TP sweep (no trail, no pyr)
    phases.append(("P2: SL/TP SWEEP (no trail)", {
        "sl_pct": [0.02, 0.03, 0.04, 0.05, 0.06, 0.07],
        "tp_pct": [0.08, 0.10, 0.15, 0.18, 0.25, 0.35],
        "pyr_trigger": [0], "pyr_add": [0],
        "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P3: Fixed trail sweep
    phases.append(("P3: FIXED TRAIL", {
        "sl_pct": [0.05],
        "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
        "trail_dist": [0.005, 0.008, 0.010, 0.015, 0.020, 0.030],
        "trail_lock": [0.0, 0.01, 0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P4: Pure trail (no TP)
    phases.append(("P4: PURE TRAIL (no TP)", {
        "sl_pct": [0.05],
        "tp_pct": [0],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.01, 0.02, 0.03, 0.04],
        "trail_dist": [0.005, 0.008, 0.010, 0.015, 0.020],
        "trail_lock": [0.0, 0.02],
        "hold_h": [168],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P5: ATR trail
    phases.append(("P5: ATR TRAIL", {
        "sl_pct": [0.05],
        "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
        "hold_h": [120],
        "atr_trail": [True],
        "atr_trail_act": [1.0, 1.5, 2.0, 3.0, 4.0],
        "atr_trail_dist": [0.5, 1.0, 1.5, 2.0, 3.0],
        "atr_trail_lock": [0.0, 0.5, 1.0],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P6: Pure ATR trail (no TP)
    phases.append(("P6: PURE ATR TRAIL (no TP)", {
        "sl_pct": [0.05],
        "tp_pct": [0],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0], "trail_dist": [0], "trail_lock": [0],
        "hold_h": [168],
        "atr_trail": [True],
        "atr_trail_act": [1.0, 2.0, 3.0, 4.0],
        "atr_trail_dist": [0.5, 1.0, 1.5, 2.0, 3.0],
        "atr_trail_lock": [0.0, 1.0],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P7: Pyramiding sweep
    phases.append(("P7: PYRAMIDING SWEEP", {
        "sl_pct": [0.05],
        "tp_pct": [0.18],
        "pyr_trigger": [0.02, 0.03, 0.05, 0.08, 0.10],
        "pyr_add": [0.3, 0.5, 0.7, 1.0],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))

    # P8: Filter sweeps (best params from above)
    phases.append(("P8a: FILTER HOURS — NONE", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))
    phases.append(("P8b: FILTER HOURS — {22,23}", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [frozenset({22,23})], "exclude_dow": [None],
    }))
    phases.append(("P8c: FILTER HOURS — {11,22,23} (orig)", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [frozenset({11,22,23})], "exclude_dow": [None],
    }))
    phases.append(("P8d: FILTER HOURS — {0,1,2,22,23}", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [frozenset({0,1,2,22,23})], "exclude_dow": [None],
    }))
    phases.append(("P8e: FILTER DOW — NONE", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [None],
    }))
    phases.append(("P8f: FILTER DOW — {2} (Wed)", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [frozenset({2})],
    }))
    phases.append(("P8g: FILTER DOW — {5,6} (weekend)", {
        "sl_pct": [0.05], "tp_pct": [0.18],
        "pyr_trigger": [0.05], "pyr_add": [0.5],
        "trail_act": [0.02], "trail_dist": [0.015], "trail_lock": [0.02],
        "hold_h": [120],
        "atr_trail": [False],
        "exclude_hours": [None], "exclude_dow": [frozenset({5,6})],
    }))

    # ── Run all phases ──
    all_results = []
    for label, param_grid in phases:
        keys = list(param_grid.keys())
        vals = list(param_grid.values())
        combos = list(itertools.product(*vals))
        n = len(combos)

        results = []
        for cv in combos:
            combo = dict(zip(keys, cv))
            # Convert frozenset to set for printing
            combo_display = {k: v for k, v in combo.items()}
            for k in combo_display:
                if isinstance(combo_display[k], frozenset):
                    combo_display[k] = set(combo_display[k])
            r = score_combo(combo, all_events, np5, np1, atr5, price_map, risk_pct, max_conc, days)
            if r:
                r["params"] = combo_display  # use display-friendly version
                results.append(r)

        results.sort(key=lambda x: x["cagr"], reverse=True)
        print_results(results, f"{label} ({n} combos)")
        all_results.extend(results)

    # ── Top N by CAGR at estimated DD ≤ 15% ──
    logger.info("\n" + "=" * 70)
    logger.info(f"VERIFYING TOP {args.verify_top} with full portfolio MTM")
    logger.info("=" * 70)

    safe = [r for r in all_results if r["mdd_est"] <= 15.0]
    safe.sort(key=lambda r: r["cagr"], reverse=True)
    top_n = safe[:args.verify_top]

    for r in top_n:
        ver = run_verification(r["params"], all_events, np5, np1, atr5, price_map, risk_pct, max_conc, days)
        if ver:
            logger.info(f"\n  {r['params']}")
            logger.info(f"  Estimated: CAGR={r['cagr']:.0f}%  DD={r['mdd_est']:.1f}%  Ret={r['total_return']:.1f}%")
            logger.info(f"  Verified:  CAGR={ver['cagr']:.0f}%  DD={ver['mdd']:.1f}%  Ret={ver['total_return']:.1f}%  "
                        f"Calmar={ver['calmar']:.1f}  Trades={ver['trades']}")
    logger.info("")