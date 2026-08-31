"""Action registry — register callable actions, resolve by name."""

from __future__ import annotations

import numpy as np
from datetime import datetime
from typing import Callable

from tqa_framework.strategy_engine.models import Signal

# fn signature: fn(symbol, price, config, state) -> list[Signal]
ActionFn = Callable[..., list[Signal]]

_registry: dict[str, ActionFn] = {}


def register_action(name: str, fn: ActionFn) -> None:
    """Register an action function by name."""
    if name in _registry:
        raise ValueError(f"Action '{name}' already registered")
    _registry[name] = fn


def get_action(name: str) -> ActionFn:
    """Look up a registered action by name."""
    if name not in _registry:
        raise KeyError(f"Unknown action: '{name}'")
    return _registry[name]


def list_actions() -> list[str]:
    """Return all registered action names."""
    return sorted(_registry.keys())


# ─── Built-in actions ────────────────────────────────────────────────────

def _open_long(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="open_long",
            direction="LONG",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _open_short(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="open_short",
            direction="SHORT",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_long(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_long",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_short(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_short",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_all(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_all",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _noop(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return []


# Register built-in actions
_builtins = [
    ("open_long", _open_long),
    ("open_short", _open_short),
    ("close_long", _close_long),
    ("close_short", _close_short),
    ("close_all", _close_all),
    ("noop", _noop),
]
for _name, _fn in _builtins:
    _registry[_name] = _fn


# ─── LSR-CROSS action: 1m hi/lo simulate_trade ──────────────────────────

def _lsr_execute(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    """Simulate LSR-CROSS trade: entry next 5m close, exit by 1m hi/lo scan.

    config requires: direction ('LONG'/'SHORT'), sl_pct, tp_pct, trail_act,
                     trail_dist, trail_lock, hold_h, pyr_trigger, pyr_add
    state requires: k5_cache, k1_cache, external_signal, symbol
    """
    k5 = state.get('k5_cache', [])
    k1 = state.get('k1_cache', [])
    ext = state.get('external_signal', {})

    if not k5 or not k1 or not ext:
        return []

    direction_int = 1 if config.get('direction', 'LONG') == 'LONG' else -1
    sl_pct = float(config.get('sl_pct', 0.05))
    tp_pct = float(config.get('tp_pct', 0.18))
    hold_h = int(config.get('hold_h', 120))
    pyr_trigger = float(config.get('pyr_trigger', 0.05))
    pyr_add = float(config.get('pyr_add', 0.5))
    trail_act_v = config.get('trail_act')
    trail_dist = float(config.get('trail_dist', 0.03))
    trail_lock = float(config.get('trail_lock', 0.02))
    trail_act = float(trail_act_v) if trail_act_v is not None else None

    def _parse_ts(val):
        if isinstance(val, datetime):
            return val if val.tzinfo is None else val.replace(tzinfo=None)
        return datetime.fromisoformat(str(val).replace('Z', '+00:00')).replace(tzinfo=None)

    ev_ts_str = ext.get('ts', '')
    ev_ts = _parse_ts(ev_ts_str)

    # Numpy arrays
    px_t5 = np.array([_parse_ts(b['ts']).timestamp() for b in k5])
    px_c5 = np.array([b['close'] for b in k5])
    ev_unix = ev_ts.timestamp()
    ei = np.searchsorted(px_t5, ev_unix, side='right')
    if ei + 1 >= len(px_c5):
        return []
    entry = float(px_c5[ei])

    px_t = np.array([_parse_ts(b['ts']).timestamp() for b in k1])
    px_h = np.array([b['high'] for b in k1])
    px_l = np.array([b['low'] for b in k1])
    px_c = np.array([b['close'] for b in k1])

    entry_ts_5m = float(px_t5[ei])
    entry_close_ts = entry_ts_5m + (px_t5[1] - px_t5[0])
    si = np.searchsorted(px_t, entry_close_ts, side='right')
    if si + 1 >= len(px_c):
        return []

    scan_mult = 60
    end_idx = min(si + int(hold_h * scan_mult), len(px_c) - 1)
    direction = direction_int
    comm_slip = 0.0010 + 0.0005
    sl_px = entry * (1 - sl_pct) if direction == 1 else entry * (1 + sl_pct)
    tp_px = entry * (1 + tp_pct) if direction == 1 else entry * (1 - tp_pct)
    pyr_px = entry * (1 + pyr_trigger) if direction == 1 else entry * (1 - pyr_trigger)
    exit_px = None
    exit_ret_unix = None
    mtm_worst = entry
    added = False
    base_risk = 1.0
    trail_active = False
    peak = entry

    for j in range(si, end_idx + 1):
        hi, lo = float(px_h[j]), float(px_l[j])
        if direction == 1:  # LONG
            mtm_worst = min(mtm_worst, lo)
            if not added and hi >= pyr_px:
                added = True
                base_risk += pyr_add
            if trail_act is not None:
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
                exit_ret_unix = px_t[j]
                break
            if not trail_active and tp_pct > 0 and hi >= tp_px:
                exit_px = float(tp_px)
                exit_ret_unix = px_t[j]
                break
        else:  # SHORT
            mtm_worst = max(mtm_worst, hi)
            if not added and lo <= pyr_px:
                added = True
                base_risk += pyr_add
            if trail_act is not None:
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
                exit_ret_unix = px_t[j]
                break
            if not trail_active and tp_pct > 0 and lo <= tp_px:
                exit_px = float(tp_px)
                exit_ret_unix = px_t[j]
                break

    if exit_px is None:
        exit_px = float(px_c[end_idx])
        exit_ret_unix = px_t[end_idx]

    if direction == 1:
        pnl = (exit_px - entry) / entry - comm_slip
        mtm_dd_pct = max(0.0, (entry - mtm_worst) / entry)
    else:
        pnl = (entry - exit_px) / entry - comm_slip
        mtm_dd_pct = max(0.0, (mtm_worst - entry) / entry)

    from datetime import datetime as _dt
    exit_dt = _dt.fromtimestamp(exit_ret_unix)
    return [Signal(
        signal_id=state.get("signal_id", "lsr_execute"),
        action="lsr_execute",
        direction='LONG' if direction == 1 else 'SHORT',
        price=float(entry),
        timestamp=str(_dt.fromtimestamp(entry_ts_5m)),
        params={
            'entry_px': float(entry),
            'exit_px': float(exit_px),
            'exit_ts': str(exit_dt),
            'exit_ts_unix': float(exit_ret_unix),
            'pnl_eff': round(float(pnl * base_risk), 6),
            'mtm_dd_pct': round(float(mtm_dd_pct * base_risk), 6),
            'base_risk': float(base_risk),
            'direction_int': float(direction),
        },
        reason=f"lsr_cross_{'LONG' if direction == 1 else 'SHORT'}",
    )]


register_action("lsr_execute", _lsr_execute)