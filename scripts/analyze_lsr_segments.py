#!/usr/bin/env python3
"""LSR-CROSS segment analysis — per-symbol, per-hour, per-DOW PnL breakdown.

Usage:
    cd /home/user/projects/tqa-framework
    source ~/.hermes/hermes-agent/venv/bin/activate
    python scripts/analyze_lsr_segments.py [--days 1095]

Output: per-symbol and per-hour PnL tables, then filtered sweep with risk=0.12
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from collections import OrderedDict
from datetime import datetime
from statistics import mean, stdev
from typing import Dict, List, Tuple

import numpy as np
import requests as _req

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("segment")

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

def load_lsr_data(symbol: str, hours: int = 24):
    import requests as _r
    end_time = get_end_time()
    lsr_query = f"""
    SELECT timestamp, ratio
    FROM crypto.long_short_ratio
    WHERE symbol='{symbol}' AND source='bybit_global'
    AND timestamp >= toDateTime64('{end_time}', 3, 'UTC') - INTERVAL {hours} HOUR
    AND timestamp <= toDateTime64('{end_time}', 3, 'UTC')
    ORDER BY timestamp
    FORMAT JSONEachRow
    """
    resp = _r.get(CH_HOST, params={"query": lsr_query}, timeout=30)
    resp.raise_for_status()
    raw = resp.text.strip()
    if not raw:
        return []

    lsr_data = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line:
            continue
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
        zprev = zscores[i-1]
        zcurr = zscores[i]
        if zprev < 2.0 and zcurr >= 2.0:
            direction = 'SHORT'
        elif zprev > -2.0 and zcurr <= -2.0:
            direction = 'LONG'
        else:
            continue
        event_ts = lsr_data[i]['timestamp']
        signals.append({
            'ts': event_ts, 'timestamp': event_ts,
            'symbol': symbol, 'direction': direction,
            'zscore': zcurr, 'zprev': zprev,
            'price_at_event': 0.0, 'action': 'signal'
        })
    return signals

def preload(days: int, end_time: str):
    k5_cache = {}
    k1_cache = {}
    np5_cache = {}
    np1_cache = {}
    price_map = {}
    all_events = []

    for symbol in TICKERS:
        if symbol in EXCLUDE_SYMS:
            continue

        bars_5m = load_m1_from_ch(symbol, days * 24, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="5m")
        if not bars_5m or len(bars_5m) < 200:
            logger.warning(f"  {symbol}: мало 5m баров, пропускаем")
            continue
        k5_cache[symbol] = bars_5m

        bars_1m = load_m1_from_ch(symbol, days * 24, CH_HOST, CH_DB, end_time=end_time, source="bars", interval="1m")
        if not bars_1m:
            bars_1m = bars_5m
        k1_cache[symbol] = bars_1m

        lsr_signals = load_lsr_data(symbol, hours=days * 24 + 720)
        if not lsr_signals:
            continue

        def _ts(v):
            if isinstance(v, str):
                return datetime.fromisoformat(v.replace("Z", "+00:00").rsplit("+")[0]).timestamp()
            return v.timestamp()
        # numpy caches
        np5_cache[symbol] = (
            np.array([_ts(b["ts"]) for b in bars_5m], dtype=np.float64),
            np.array([b["close"] for b in bars_5m], dtype=np.float64),
        )
        np1_cache[symbol] = (
            np.array([_ts(b["ts"]) for b in bars_1m], dtype=np.float64),
            np.array([b["high"] for b in bars_1m], dtype=np.float64),
            np.array([b["low"] for b in bars_1m], dtype=np.float64),
            np.array([b["close"] for b in bars_1m], dtype=np.float64),
        )
        price_map[symbol] = OrderedDict(
            (parse_ts(b["ts"]), b["close"]) for b in bars_1m
        )

        for sig in lsr_signals:
            ts = sig["timestamp"]
            ts_dt = parse_ts(ts)
            if ts_dt < parse_ts(end_time).replace(tzinfo=None) - __import__('datetime').timedelta(hours=days * 24):
                continue
            d = 1 if sig["direction"] == "LONG" else -1
            all_events.append((ts_dt, symbol, d))

    logger.info(f"Preloaded: {len(k5_cache)} symbols, {len(all_events)} events total")
    return k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events

def simulate_trade(symbol, event_ts, direction_int,
                   sl_pct, tp_pct, hold_h,
                   pyr_trigger, pyr_add,
                   trail_act, trail_dist, trail_lock,
                   np5_cache, np1_cache):
    if symbol not in np5_cache or symbol not in np1_cache:
        return None
    
    px_t5, px_c5 = np5_cache[symbol]
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
    has_trail = trail_act is not None and trail_act > 0
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
                if not trail_active and hi >= entry * (1 + trail_act):
                    trail_active = True
                    peak = hi
                    sl_px = entry
                if trail_active:
                    if hi > peak:
                        peak = hi
                    new_sl = float(peak * (1 - trail_dist))
                    if trail_lock > 0:
                        new_sl = max(new_sl, entry * (1 + trail_lock))
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
                if not trail_active and lo <= entry * (1 - trail_act):
                    trail_active = True
                    peak = lo
                    sl_px = entry
                if trail_active:
                    if lo < peak:
                        peak = lo
                    new_sl = float(peak * (1 + trail_dist))
                    if trail_lock > 0:
                        new_sl = min(new_sl, entry * (1 - trail_lock))
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


def analyze_segments(preloaded, best_params, risk_pct=0.08, max_conc=6, initial_equity=1000.0, leverage=1.0):
    k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events = preloaded
    
    sl_pct = best_params.get("sl_pct", 0.05)
    tp_pct = best_params.get("tp_pct", 0.18)
    hold_h = best_params.get("hold_h", 120)
    pyr_trigger = best_params.get("pyr_trigger", 0.05)
    pyr_add = best_params.get("pyr_add", 0.5)
    trail_act = best_params.get("trail_act", 0.02)
    trail_dist = best_params.get("trail_dist", 0.015)
    trail_lock = best_params.get("trail_lock", 0.02)
    
    # Simulate all trades, track per-symbol and per-hour stats
    trade_details = []
    for event_dt, sym, d in all_events:
        if sym not in k5_cache or sym not in k1_cache:
            continue
        res = simulate_trade(sym, event_dt, d,
                            sl_pct, tp_pct, hold_h,
                            pyr_trigger, pyr_add,
                            trail_act, trail_dist, trail_lock,
                            np5_cache, np1_cache)
        if res is None:
            continue
        pnl_eff, _, entry_px, exit_px, direction, base_risk, exit_ts = res
        hour = event_dt.hour
        dow = event_dt.weekday()
        trade_details.append({
            "symbol": sym,
            "direction": direction,
            "entry_ts": event_dt,
            "hour": hour,
            "dow": dow,
            "pnl_eff": pnl_eff,
            "base_risk": base_risk,
            "entry_px": entry_px,
            "exit_px": exit_px,
        })
    
    # Per-symbol analysis
    logger.info("\n" + "=" * 60)
    logger.info("PER-SYMBOL PnL ANALYSIS")
    logger.info("=" * 60)
    sym_stats = {}
    for t in trade_details:
        sym = t["symbol"]
        if sym not in sym_stats:
            sym_stats[sym] = {"pnls": [], "n": 0, "wins": 0}
        pnl = t["pnl_eff"]
        sym_stats[sym]["pnls"].append(pnl)
        sym_stats[sym]["n"] += 1
        if pnl > 0:
            sym_stats[sym]["wins"] += 1
    
    sym_rows = []
    for sym, st in sorted(sym_stats.items(), key=lambda x: sum(x[1]["pnls"]), reverse=True):
        avg = mean(st["pnls"])
        wr = st["wins"] / st["n"] * 100
        total_pnl = sum(st["pnls"])
        sym_rows.append((sym, st["n"], total_pnl, avg, wr))
    
    logger.info(f"{'Symbol':>10} {'Trades':>6} {'TotalPnL':>10} {'Avg':>10} {'WR%':>6}")
    logger.info("-" * 50)
    for r in sym_rows:
        logger.info(f"{r[0]:>10} {r[1]:>6} {r[2]:>+10.4f} {r[3]:>+10.4f} {r[4]:>5.1f}%")
    
    # Cumulative total
    cum_pnl = sum(sum(st["pnls"]) for st in sym_stats.values())
    logger.info(f"{'TOTAL':>10} {sum(r[1] for r in sym_rows):>6} {sum(r[2] for r in sym_rows):>+10.4f}")
    
    # Per-hour analysis (entry hour)
    logger.info("\n" + "=" * 60)
    logger.info("PER-HOUR (ENTRY) PnL ANALYSIS")
    logger.info("=" * 60)
    hour_stats = {}
    for t in trade_details:
        h = t["hour"]
        if h not in hour_stats:
            hour_stats[h] = {"pnls": [], "n": 0, "wins": 0}
        pnl = t["pnl_eff"]
        hour_stats[h]["pnls"].append(pnl)
        hour_stats[h]["n"] += 1
        if pnl > 0:
            hour_stats[h]["wins"] += 1
    
    hour_rows = []
    for h in sorted(hour_stats.keys()):
        st = hour_stats[h]
        avg = mean(st["pnls"])
        wr = st["wins"] / st["n"] * 100
        total_pnl = sum(st["pnls"])
        hour_rows.append((h, st["n"], total_pnl, avg, wr))
    
    logger.info(f"{'Hour':>5} {'Trades':>6} {'TotalPnL':>10} {'Avg':>10} {'WR%':>6}")
    logger.info("-" * 50)
    for r in hour_rows:
        logger.info(f"{r[0]:>5} {r[1]:>6} {r[2]:>+10.4f} {r[3]:>+10.4f} {r[4]:>5.1f}%")
    
    # Per-DOW analysis
    logger.info("\n" + "=" * 60)
    logger.info("PER-DOW PnL ANALYSIS")
    logger.info("=" * 60)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_stats = {}
    for t in trade_details:
        d = t["dow"]
        if d not in dow_stats:
            dow_stats[d] = {"pnls": [], "n": 0, "wins": 0}
        pnl = t["pnl_eff"]
        dow_stats[d]["pnls"].append(pnl)
        dow_stats[d]["n"] += 1
        if pnl > 0:
            dow_stats[d]["wins"] += 1
    
    dow_rows = []
    for d in sorted(dow_stats.keys()):
        st = dow_stats[d]
        avg = mean(st["pnls"])
        wr = st["wins"] / st["n"] * 100
        total_pnl = sum(st["pnls"])
        dow_rows.append((dow_names[d], st["n"], total_pnl, avg, wr))
    
    logger.info(f"{'DOW':>5} {'Trades':>6} {'TotalPnL':>10} {'Avg':>10} {'WR%':>6}")
    logger.info("-" * 50)
    for r in dow_rows:
        logger.info(f"{r[0]:>5} {r[1]:>6} {r[2]:>+10.4f} {r[3]:>+10.4f} {r[4]:>5.1f}%")
    
    return trade_details, sym_rows, hour_rows

def run_filtered_sweep(preloaded, tickers, exclude_hours, exclude_dow,
                        risk_pct=0.08, max_conc=6, initial_equity=1000.0, leverage=1.0,
                        param_grid=None):
    """Run sweep with filtered symbols/hours."""
    k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events = preloaded
    
    if param_grid is None:
        param_grid = {
            "sl_pct": [0.05],
            "tp_pct": [0],
            "pyr_trigger": [0.05],
            "pyr_add": [0.5],
            "trail_act": [0.04],
            "trail_dist": [0.015, 0.03],
            "trail_lock": [0, 0.02],
            "hold_h": [168],
        }
    
    # Filter events
    filtered_events = []
    for event_dt, sym, d in all_events:
        if sym not in tickers:
            continue
        if event_dt.hour in exclude_hours:
            continue
        if event_dt.weekday() in exclude_dow:
            continue
        filtered_events.append((event_dt, sym, d))
    
    logger.info(f"\nFiltered events: {len(filtered_events)} (from {len(all_events)}), "
                f"risk={risk_pct}, conc={max_conc}")
    
    param_keys = list(param_grid.keys())
    param_values = list(param_grid.values())
    n_combos = len(list(itertools.product(*param_values)))
    
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
        trail_lock = combo.get("trail_lock", 0.02)
        
        # Simulate trades
        trade_results = []
        for event_dt, sym, d in filtered_events:
            res = simulate_trade(sym, event_dt, d,
                                sl_pct, tp_pct, hold_h,
                                pyr_trigger, pyr_add,
                                trail_act, trail_dist, trail_lock,
                                np5_cache, np1_cache)
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
                "entry_ts": event_dt,
                "symbol": sym,
                "direction": direction,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "exit_ts_converted": exit_ts,
                "base_risk": base_risk,
                "pnl_eff": pnl_eff,
                "eq_at_entry": None,
                "opened": False,
            })
        
        positions.sort(key=lambda p: p["entry_ts"])
        all_ts = sorted(set().union(*[price_map[s].keys() for s in price_map if s in k5_cache]))
        next_pos_idx = 0
        active = []
        
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
            "cagr": round(total_return / (365 * len(filtered_events) / (365*24*60/5) / 3), 1),
            "trades": opened_trades,
            "calmar": round(total_return / max_dd, 2) if max_dd > 0 else 0,
        })
    
    # Sort
    dd_threshold = 15.0
    safe = [r for r in results if r["mdd"] <= dd_threshold]
    unsafe = [r for r in results if r["mdd"] > dd_threshold]
    safe.sort(key=lambda r: r["cagr"], reverse=True)
    unsafe.sort(key=lambda r: r["cagr"], reverse=True)
    
    return safe + unsafe


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    args = parser.parse_args()
    days = args.days
    end_time = get_end_time()
    
    logger.info(f"End time: {end_time}, days={days}")
    logger.info("Preloading data...")
    data = preload(days, end_time)
    
    # ── Best config from sweep ──
    best_pure_trail = {
        "sl_pct": 0.05, "tp_pct": 0, "pyr_trigger": 0.05, "pyr_add": 0.5,
        "trail_act": 0.04, "trail_dist": 0.015, "trail_lock": 0, "hold_h": 168,
    }
    best_atr_trail = {
        "sl_pct": 0.05, "tp_pct": 0.18, "pyr_trigger": 0.05, "pyr_add": 0.5,
        "trail_act": 0.04, "trail_dist": 0.03, "trail_lock": 0, "hold_h": 120,
    }
    
    # Phase 1: Analyze segments with best pure trail config
    logger.info("\n" + "#" * 70)
    logger.info("# PHASE A: SEGMENT ANALYSIS WITH BEST PURE TRAIL CONFIG")
    logger.info("#" * 70)
    trade_details, sym_rows, hour_rows = analyze_segments(data, best_pure_trail, 0.08, 6)
    
    # Identify weak symbols (bottom 25% by total PnL)
    sorted_syms = sorted(sym_rows, key=lambda r: r[2])  # by total PnL ascending
    weak_syms = set(r[0] for r in sorted_syms[:len(sorted_syms) // 4])  # bottom 25%
    
    # Identify weak hours (negative total PnL or bottom)
    sorted_hours = sorted(hour_rows, key=lambda r: r[2])  # by total PnL ascending
    weak_hours = set(r[0] for r in sorted_hours if r[2] < -0.5)  # negative
    
    logger.info(f"\nWeak symbols (bottom 25%): {weak_syms}")
    logger.info(f"Weak hours (negative PnL): {weak_hours}")
    
    # ── Phase 2: same with ATR-trail config ──
    logger.info("\n" + "#" * 70)
    logger.info("# PHASE B: SEGMENT ANALYSIS WITH BEST ATR-TRAIL CONFIG")
    logger.info("#" * 70)
    _, sym_rows2, hour_rows2 = analyze_segments(data, best_atr_trail, 0.08, 6)
    
    sorted_syms2 = sorted(sym_rows2, key=lambda r: r[2])
    weak_syms2 = set(r[0] for r in sorted_syms2[:len(sorted_syms2) // 4])
    sorted_hours2 = sorted(hour_rows2, key=lambda r: r[2])
    weak_hours2 = set(r[0] for r in sorted_hours2 if r[2] < -0.5)
    
    logger.info(f"\nWeak symbols (ATR-trail, bottom 25%): {weak_syms2}")
    logger.info(f"Weak hours (ATR-trail, negative PnL): {weak_hours2}")
    
    # Intersection: consistently weak segments
    drop_syms = weak_syms & weak_syms2
    drop_hours = weak_hours | weak_hours2  # union — any hour negative in either
    logger.info(f"\nConsistently weak symbols (both configs): {drop_syms}")
    logger.info(f"Hours to drop (negative in either): {drop_hours}")
    
    if not drop_syms and not drop_hours:
        logger.info("\nNo clear weak segments found. Trying concentrated symbols...")
        # Try top 6 symbols only
        top_syms = set(r[0] for r in sorted(sym_rows, key=lambda x: x[2], reverse=True)[:6])
        logger.info(f"Top 6 symbols: {top_syms}")
        filtered_tickers = top_syms
        extra_hours = set()
    else:
        # Keep only non-weak symbols
        active_syms = set(r[0] for r in sym_rows if r[0] not in drop_syms)
        filtered_tickers = active_syms
        extra_hours = drop_hours
    
    current_hours = {11, 22, 23}
    all_exclude_hours = current_hours | extra_hours
    
    # ── Phase 3: Filtered sweep at elevated risk ──
    logger.info("\n" + "#" * 70)
    logger.info("# PHASE C: FILTERED SWEEP WITH ELEVATED RISK")
    logger.info("#" * 70)
    
    for risk_val in [0.10, 0.12, 0.15]:
        logger.info(f"\n--- risk={risk_val}, symbols={filtered_tickers}, exclude_hours={all_exclude_hours} ---")
        res = run_filtered_sweep(
            data, filtered_tickers, all_exclude_hours, {2},  # keep existing DOW exclude
            risk_pct=risk_val, max_conc=min(6, len(filtered_tickers)),
            param_grid={
                "sl_pct": [0.05],
                "tp_pct": [0, 0.18],
                "pyr_trigger": [0.05],
                "pyr_add": [0.5],
                "trail_act": [0.04],
                "trail_dist": [0.015, 0.03],
                "trail_lock": [0, 0.02],
                "hold_h": [120, 168],
            }
        )
        for r in res[:5]:
            safe = "✓" if r["mdd"] <= 15.0 else ""
            logger.info(f"  risk={risk_val:.2f} → CAGR≈{r['cagr']/3:>5.1f}% real  Ret {r['total_return']:>7.2f}%  DD {r['mdd']:>5.1f}%  Calmar {r['calmar']:>5.1f}  Trd {r['trades']:>4d}  {safe}  {r['params']}")
    
    # ── Attempt: risk=0.08 on top-4 symbols with no hour filter ──
    logger.info("\n" + "#" * 70)
    logger.info("# PHASE D: CONCENTRATION — TOP SYMBOLS ONLY")
    logger.info("#" * 70)
    top4 = set(r[0] for r in sorted(sym_rows, key=lambda x: x[2], reverse=True)[:4])
    logger.info(f"Top 4 by PnL: {top4}")
    for risk_val in [0.08, 0.12, 0.15, 0.20]:
        res = run_filtered_sweep(
            data, top4, set(), {2},
            risk_pct=risk_val, max_conc=4,
            param_grid={
                "sl_pct": [0.05],
                "tp_pct": [0, 0.18],
                "pyr_trigger": [0.05],
                "pyr_add": [0.5],
                "trail_act": [0.04],
                "trail_dist": [0.015],
                "trail_lock": [0],
                "hold_h": [168],
            }
        )
        for r in res[:3]:
            safe = "✓" if r["mdd"] <= 15.0 else ""
            logger.info(f"  risk={risk_val:.2f} top4 → CAGR≈{r['cagr']/3:>5.1f}% real  Ret {r['total_return']:>7.2f}%  DD {r['mdd']:>5.1f}%  Calmar {r['calmar']:>5.1f}  {safe}  {r['params']}")
    
    # ── Phase E: Try risk=0.12 with only negative hours filtered ──
    union_bad_hours = {h for r in [hour_rows, hour_rows2] for h, _, tot, _, _ in r if tot < -0.3}
    logger.info(f"\nUnion of bad hours from both configs: {union_bad_hours}")
    
    logger.info("\n" + "#" * 70)
    logger.info("# PHASE E: REMOVE BAD HOURS + ALL SYMBOLS, risk=0.12")
    logger.info("#" * 70)
    res = run_filtered_sweep(
        data, set(r[0] for r in sym_rows), current_hours | union_bad_hours, {2},
        risk_pct=0.12, max_conc=6,
        param_grid={
            "sl_pct": [0.05],
            "tp_pct": [0, 0.18],
            "pyr_trigger": [0.05],
            "pyr_add": [0.5],
            "trail_act": [0.04],
            "trail_dist": [0.015, 0.03],
            "trail_lock": [0, 0.02],
            "hold_h": [120, 168],
        }
    )
    for r in res[:5]:
        safe = "✓" if r["mdd"] <= 15.0 else ""
        logger.info(f"  risk=0.12 → CAGR≈{r['cagr']/3:>5.1f}% real  Ret {r['total_return']:>7.2f}%  DD {r['mdd']:>5.1f}%  Calmar {r['calmar']:>5.1f}  {safe}  {r['params']}")