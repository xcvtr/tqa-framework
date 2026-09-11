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

    # Numpy arrays (pre-built caches from state if provided — avoids O(events×bars) rebuild)
    np5 = state.get('np5_cache')   # (ts5[], c5[]) or None
    np1 = state.get('np1_cache')   # (t1[], h1[], lo1[], c1[]) or None
    if np5 is not None:
        px_t5, px_c5 = np5
    else:
        px_t5 = np.array([_parse_ts(b['ts']).timestamp() for b in k5])
        px_c5 = np.array([b['close'] for b in k5])
    ev_unix = ev_ts.timestamp()

    if np1 is not None:
        px_t, px_h, px_l, px_c = np1
    else:
        px_t = np.array([_parse_ts(b['ts']).timestamp() for b in k1])
        px_h = np.array([b['high'] for b in k1])
        px_l = np.array([b['low'] for b in k1])
        px_c = np.array([b['close'] for b in k1])

    # Live entry (tick.py opens at last_close = first 1m close at/after event).
    # NOT the next 5m close — live enters on the next 1m tick.
    si = np.searchsorted(px_t, ev_unix, side='right')
    if si + 1 >= len(px_c):
        return []
    entry = float(px_c[si])
    entry_ts_1m = float(px_t[si])
    start_idx = si + 1  # scan from the bar AFTER entry (entry taken at close of bar si)

    scan_mult = 60
    end_idx = min(start_idx + int(hold_h * scan_mult), len(px_c) - 1)
    direction = direction_int
    # comm/slip из конфига (live: PG strategy_config comm=0.001, slip=0.0005).
    # YAML risk.comission/risk.slippage подключаются в _act_params в _run_lsr_mode.
    comm_slip = float(config.get('comm', 0.001)) + float(config.get('slip', 0.0005))
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

    for j in range(start_idx, end_idx + 1):
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
        timestamp=str(_dt.fromtimestamp(entry_ts_1m)),
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


# ─── CROSS action: LEAD external cross → LAG time-hold trade (forex pips) ────
# 1:1 с /tmp/fx_eur_gbp_maker.py (config 30m_maker_ny_z3):
#   сигнал z-кросса строится по LEAD-бару (внешний; действия берут ts и direction),
#   позиция открывается по LAG-паре из 1m закрытий. Entry = первый LAG 1m close
#   на/после ts события (≈ close 30m-бара события), exit = close через hold_h часов.
#   cost = comm+slip (фракция round-trip, 0.2 pips = 2e-5).

def _cross_execute(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    """Cross-pair trade, 1:1 с /tmp/fx_eur_gbp_maker.py (30m_maker_ny_z3).

    signal = z-кросс по LEAD (external_signal: ts RIGHT-labeled 30m бар события, direction).
    Позиция по LAG (symbol): entry = LAG 30m close на ts события (maker B[valid]),
    exit = LAG 30m close через hold_h часов (maker B[valid+hold_bars]). cost = comm+slip.
    Adverse M1-MTM DD по 1m hi/lo LAG за период hold (честный DD).

    config requires: hold_h (hours); comm, slip (fractional round-trip).
    state requires: external_signal {ts, direction}; lag30_close {ts_str: close} —
                    pandas right-labeled 30m closes LAG (как в maker). np1/k1 опционально.
    symbol is the LAG (traded) pair.
    """
    import numpy as np
    from datetime import datetime as _dt, timedelta as _td

    ext = state.get('external_signal', {})
    lag30 = state.get('lag30_close', {})
    if not ext or not lag30:
        return []

    direction = -1 if str(ext.get('direction', 'LONG')).upper() == 'SHORT' else 1
    hold_h = float(config.get('hold_h', 1.0))
    comm_slip = float(config.get('comm', 0.0)) + float(config.get('slip', 0.0))

    def _parse_ts(val):
        if isinstance(val, _dt):
            return val if val.tzinfo is None else val.replace(tzinfo=None)
        return _dt.fromisoformat(str(val).replace('Z', '+00:00').replace(' ', 'T')).replace(tzinfo=None)

    ev = _parse_ts(ext.get('ts', ''))
    entry_ts = str(ev)
    exit_ts = str(ev + _td(minutes=max(1, round(hold_h * 60))))
    if entry_ts not in lag30 or exit_ts not in lag30:
        return []
    entry = float(lag30[entry_ts])
    exit_px = float(lag30[exit_ts])

    # Adverse MTM DD по 1m hi/lo LAG между entry и exit (честный M1-MTM DD).
    mtm_dd_pct = 0.0
    np1 = state.get('np1_cache')
    if np1 is not None:
        px_t, px_h, px_l, _px_c = np1
        e0 = int(np.searchsorted(px_t, ev.timestamp(), side='left'))
        e1 = int(np.searchsorted(px_t, (ev + _td(minutes=max(1, round(hold_h * 60)))).timestamp(), side='left'))
        if e1 > e0:
            if direction == 1:
                mtm_dd_pct = max(0.0, (entry - float(np.min(px_l[e0:e1]))) / entry)
            else:
                mtm_dd_pct = max(0.0, (float(np.max(px_h[e0:e1])) - entry) / entry)

    if direction == 1:
        pnl = (exit_px - entry) / entry - comm_slip
    else:
        pnl = (entry - exit_px) / entry - comm_slip

    return [Signal(
        signal_id=state.get("signal_id", "cross_execute"),
        action="cross_execute",
        direction='LONG' if direction == 1 else 'SHORT',
        price=float(entry),
        timestamp=str(entry_ts),
        params={
            'entry_px': float(entry),
            'exit_px': float(exit_px),
            'exit_ts': str(exit_ts),
            'exit_ts_unix': float(_dt.fromisoformat(exit_ts.replace(' ', 'T')).timestamp()),
            'pnl_eff': round(float(pnl), 6),
            'mtm_dd_pct': round(float(mtm_dd_pct), 6),
            'base_risk': 1.0,
            'direction_int': float(direction),
        },
        reason=f"cross_{('LONG' if direction == 1 else 'SHORT')}",
    )]


register_action("cross_execute", _cross_execute)