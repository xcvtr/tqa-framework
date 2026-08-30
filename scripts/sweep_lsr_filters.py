#!/usr/bin/env python3
"""LSR-CROSS filter sweep — standalone, copies simulate_trade from sweep_lsr_cross.
Preloads once with NO filters, then tests all hour/DOW combos.

Usage:
    cd /home/user/projects/tqa-framework
    source ~/.hermes/hermes-agent/venv/bin/activate
    python scripts/sweep_lsr_filters.py [--days 1095] [--risk 0.08] [--conc 6]
"""
import argparse, itertools, logging, sys, time
from collections import OrderedDict
from datetime import datetime

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("filter-sweep")

sys.path.insert(0, "/home/user/projects/tqa-framework")
import sweep_lsr_cross as sweep

# ── Filter grid ──
HOUR_GRID = [
    {11, 22, 23},   # current default
    set(),           # no hour filter
    {11},            # only midday
    {22, 23},        # late night
    {0, 1, 2},       # early morning
    {11, 12, 13},    # extended midday
    {21, 22, 23},    # evening
    {3, 4, 5},       # night
]

DOW_GRID = [
    {2},             # current (Tue)
    set(),           # no DOW filter
    {1, 2},          # Mon-Tue
    {4, 5},          # Thu-Fri
    {0, 6},          # weekend
    {3},             # only Wed
]

# ── Standalone simulate_trade (copied from sweep_lsr_cross.py, uses np5_cache, np1_cache) ──

def simulate_trade(symbol, event_ts, direction_int,
                   np5_cache, np1_cache,
                   sl_pct, tp_pct, hold_h,
                   pyr_trigger, pyr_add,
                   trail_act, trail_dist, trail_lock):
    """Fast simulate_trade using pre-built numpy caches."""
    if symbol not in np5_cache or symbol not in np1_cache:
        return None
    
    px_t5, px_c5 = np5_cache[symbol]
    px_t, px_h, px_l, px_c = np1_cache[symbol]
    
    ev_ts = sweep.parse_ts(event_ts).timestamp()
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


def run_filter_sweep(k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events,
                     risk_pct, max_conc, base_params):
    """Sweep filter combos with fixed strategy params."""
    results = []
    for hours in HOUR_GRID:
        for dow in DOW_GRID:
            label = f"H{''.join(str(h) for h in sorted(hours)) or 'all'}_D{''.join(str(d) for d in sorted(dow)) or 'all'}"
            
            dt0 = time.time()
            
            # Filter events
            filtered = []
            for event_dt, sym, d in all_events:
                if sym in sweep.EXCLUDE_SYMS:
                    continue
                if hours and event_dt.hour in hours:
                    continue
                if dow and event_dt.weekday() in dow:
                    continue
                filtered.append((event_dt, sym, d))
            
            if not filtered:
                logger.info(f"  {label} — NO EVENTS")
                continue
            
            # Simulate all trades with this filter set
            trade_results = []
            for event_dt, sym, d in filtered:
                res = simulate_trade(sym, event_dt, d,
                    np5_cache, np1_cache,
                    base_params["sl_pct"], base_params["tp_pct"], base_params["hold_h"],
                    base_params["pyr_trigger"], base_params["pyr_add"],
                    base_params["trail_act"], base_params["trail_dist"], base_params["trail_lock"])
                if res is None:
                    continue
                trade_results.append((event_dt, sym, d, res))
            
            if not trade_results:
                logger.info(f"  {label} — NO TRADES")
                continue
            
            # ── Portfolio MTM (same logic as run_sweep) ──
            INIT_EQ = 1000.0
            eq = float(INIT_EQ)
            cash = eq
            peak = eq
            max_dd = 0.0
            
            sym_risk = {
                "ADAUSDT": 1.3, "ARBUSDT": 1.2, "OPUSDT": 1.1, "AVAXUSDT": 1.05,
                "DOGEUSDT": 1.0, "XRPUSDT": 1.0, "ETHUSDT": 1.0,
                "BNBUSDT": 1.0, "LINKUSDT": 1.0, "NEARUSDT": 0.9, "APTUSDT": 0.9,
            }
            
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
            active = []
            
            for bar_ts in all_ts:
                while next_pos_idx < len(positions):
                    p = positions[next_pos_idx]
                    if p["entry_ts"] <= bar_ts:
                        if len(active) < max_conc:
                            p["eq_at_entry"] = float(eq)
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
                        r = risk_pct * sym_risk.get(p["symbol"], 1.0)
                        pnl_dollars = p["eq_at_entry"] * r * p["pnl_eff"]
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
                        r = risk_pct * sym_risk.get(p["symbol"], 1.0)
                        if p["direction"] == 1:
                            mtm_pnl = (price - p["entry_px"]) / p["entry_px"]
                        else:
                            mtm_pnl = (p["entry_px"] - price) / p["entry_px"]
                        port_value += p["eq_at_entry"] * r * mtm_pnl
                
                eq = port_value
                if port_value > peak:
                    peak = port_value
                dd = (peak - port_value) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            
            for p in positions:
                if p["opened"] and p.get("eq_at_entry") is not None:
                    r = risk_pct * sym_risk.get(p["symbol"], 1.0)
                    pnl_dollars = p["eq_at_entry"] * r * p["pnl_eff"]
                    cash += pnl_dollars
            
            total_return = (cash - INIT_EQ) / INIT_EQ * 100
            opened = sum(1 for p in positions if p["opened"])
            days = 1095
            cagr = total_return / (days / 365)
            calmar = total_return / max_dd if max_dd > 0 else 0
            
            results.append({
                "label": label,
                "hours": hours,
                "dow": dow,
                "events": len(trade_results),
                "opened": opened,
                "total_return": round(total_return, 2),
                "mdd": round(max_dd, 2),
                "cagr": round(cagr, 1),
                "calmar": round(calmar, 2),
                "time": round(time.time() - dt0, 1),
            })
    
    results.sort(key=lambda r: r["cagr"], reverse=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--risk", type=float, default=0.08)
    parser.add_argument("--conc", type=int, default=6)
    args = parser.parse_args()
    days = args.days
    risk_pct = args.risk
    max_conc = args.conc
    
    # Preload with NO filters (empty all sets)
    original_hours = sweep.EXCLUDE_HOURS
    original_dow = sweep.EXCLUDE_DOW
    sweep.EXCLUDE_HOURS = set()
    sweep.EXCLUDE_DOW = set()
    
    end_time = sweep.get_end_time(sweep.CH_HOST, sweep.CH_DB)
    logger.info(f"Preloading data (no filters)... end={end_time}")
    k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events = sweep.preload(days, end_time)
    logger.info(f"Loaded {len(all_events)} unfiltered events, {len(k5_cache)} symbols")
    
    # Restore originals
    sweep.EXCLUDE_HOURS = original_hours
    sweep.EXCLUDE_DOW = original_dow
    
    packed = (k5_cache, k1_cache, np5_cache, np1_cache, price_map, all_events)
    
    # Best config: ATR-trail + pyr
    params_atr = {
        "sl_pct": 0.05, "tp_pct": 0.18,
        "pyr_trigger": 0.05, "pyr_add": 0.5,
        "trail_act": 0.04, "trail_dist": 0.03, "trail_lock": 0,
        "hold_h": 120,
    }
    # Pure trail config
    params_pure = {
        "sl_pct": 0.05, "tp_pct": 0,
        "pyr_trigger": 0.05, "pyr_add": 0.5,
        "trail_act": 0.04, "trail_dist": 0.015, "trail_lock": 0,
        "hold_h": 168,
    }
    
    logger.info("\n" + "=" * 70)
    logger.info("FILTER SWEEP: ATR-TRAIL (trail_act=0.04, trail_dist=0.03)")
    logger.info("=" * 70)
    r1 = run_filter_sweep(*packed, risk_pct, max_conc, params_atr)
    logger.info(f"{'Filter':>18}  {'Events':>6}  {'Opened':>6}  {'CAGR':>6}  {'DD%':>6}  {'Ret%':>8}  {'Calmar':>7}")
    logger.info("-" * 65)
    for r in r1:
        tag = " ✓" if r["mdd"] <= 15 else " "
        logger.info(f"{r['label']:>18}  {r['events']:>6}  {r['opened']:>6}  {r['cagr']:>5.0f}%  {r['mdd']:>5.1f}%  {r['total_return']:>7.2f}%  {r['calmar']:>7.1f}{tag}")
    
    logger.info("\n" + "=" * 70)
    logger.info("FILTER SWEEP: PURE TRAIL (trail_act=0.04, trail_dist=0.015)")
    logger.info("=" * 70)
    r2 = run_filter_sweep(*packed, risk_pct, max_conc, params_pure)
    logger.info(f"{'Filter':>18}  {'Events':>6}  {'Opened':>6}  {'CAGR':>6}  {'DD%':>6}  {'Ret%':>8}  {'Calmar':>7}")
    logger.info("-" * 65)
    for r in r2:
        tag = " ✓" if r["mdd"] <= 15 else " "
        logger.info(f"{r['label']:>18}  {r['events']:>6}  {r['opened']:>6}  {r['cagr']:>5.0f}%  {r['mdd']:>5.1f}%  {r['total_return']:>7.2f}%  {r['calmar']:>7.1f}{tag}")
    logger.info("")