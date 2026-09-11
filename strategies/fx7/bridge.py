"""FX-7 проект: мост стратегия → MT5-контур (по образцу paper_trader).

Обёртки над tqa_framework.engine.exchange_mt5_bridge:
  - write_mt5_signals: запись ENTER-сигналов в fx_top.mt5_signals
  - sync_mt5: обратная синхронизация позиций MT5 → PG state
    (с сохранением pyramid_added/entry_time), детект закрытых сделок.

Функции чистые/инъективные (conn или pg_factory) — удобно тестировать без
живого MT5 и прод-PG.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Optional

import psycopg2

from tqa_framework.engine.exchange_mt5_bridge import (
    DEFAULT_PG, MT5_SYM_REVERSE, insert_signal, next_signal_id, reverse_sym,
)

# PG → MT5 (обратная карта для запросов к audit_trades и пр.)
MT5_SYM = {v: k for k, v in MT5_SYM_REVERSE.items()}


@contextmanager
def _open(conn=None, pg_factory: Optional[Callable] = None):
    """Отдать conn, если передан; иначе открыть новое подключение из factory/env."""
    if conn is not None:
        yield conn
    elif pg_factory is not None:
        with pg_factory() as c:
            yield c
    else:
        pg = dict(DEFAULT_PG)
        pg.pop("schema", None)
        with psycopg2.connect(**pg, connect_timeout=5) as c:
            yield c


def write_mt5_signals(positions_opened: list[dict], conn=None, pg_factory=None,
                      schema: str = "fx_top") -> list[str]:
    """Записать ENTER-сигналы для MT5-bridge (как paper_trader.write_mt5_signals).

    positions_opened: list[dict] с ключами sym/direction/entry_price и опц.
    sl_pips/tp_pips/lot (или lot_size). Возвращает список записанных id.
    """
    ids = []
    for pos in positions_opened:
        action = pos["direction"]
        sym = pos["sym"].upper()
        entry = pos["entry_price"]
        lot = max(0.01, round(float(pos.get("lot", pos.get("lot_size", 0.01))) / 0.01) * 0.01)
        sig_id = next_signal_id()
        row = dict(
            id=sig_id, type="ENTER", action=action, symbol=sym, entry=entry,
            sl_pips=pos.get("sl_pips", 30), tp_pips=pos.get("tp_pips", 60),
            lot_size=lot,
        )
        with _open(conn=conn, pg_factory=pg_factory) as c:
            insert_signal(c, schema, row)
        ids.append(sig_id)
    return ids


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_mt5(state: dict, mt5_positions: list[dict],
             closed_info: Optional[dict] = None) -> dict:
    """MT5 → PG state (как paper_trader.sync_mt5), чистая функция.

    state — dict с positions/closed_trades/equity.
    mt5_positions — list из fx_top.mt5_account.positions (JSONB).
    closed_info — dict symbol → (pnl_usd, exit_price) закрытых сделок (из audit);
                  None → закрытие помечается reason='MT5_SL' без PnL.
    Возвращает НОВЫЙ state (исходный не мутируется).
    """
    out = dict(state)
    old_pos = {p["sym"]: p for p in out.get("positions", [])}
    closed_info = closed_info or {}

    pg_positions = []
    for p in mt5_positions:
        sym = reverse_sym(p.get("symbol", ""))
        old = old_pos.get(sym, {})
        pg_positions.append({
            "sym": sym,
            "direction": "BUY" if str(p.get("type", "")).lower() == "buy" else "SELL",
            "entry_price": float(p.get("price_open", 0) or 0),
            "entry_time": old.get("entry_time") or _now_utc(),
            "lot": float(p.get("volume", 0.01) or 0.01),
            "sl_pips": old.get("sl_pips", 50),
            "tp_pips": old.get("tp_pips", 0),
            "trail_activation": old.get("trail_activation", 1.5),
            "trail_trail": old.get("trail_trail", 0.8),
            "best_pnl_pct": old.get("best_pnl_pct", 0.0),
            "cluster_level": old.get("cluster_level", 0.0),
            "pyramid_added": old.get("pyramid_added", 0),  # не сбрасывать при sync!
        })

    new_syms = {p["sym"] for p in pg_positions}
    closed = list(out.get("closed_trades", []))
    equity = float(out.get("equity", 0) or 0) or 1.0
    for sym, old in old_pos.items():
        if sym not in new_syms:
            info = closed_info.get(sym)
            pnl_usd = float(info[0]) if info else None
            exit_px = float(info[1]) if info else 0.0
            closed.append({
                "sym": sym, "direction": old.get("direction", ""),
                "pnl_usd": pnl_usd,
                "pnl_pct": round(pnl_usd / equity * 100, 2) if pnl_usd is not None else 0,
                "pnl_pips": 0, "entry": old.get("entry_price", 0), "exit": exit_px,
                "entry_time": old.get("entry_time", ""), "exit_time": _now_utc(),
                "reason": "MT5_SL", "hold_hours": 0,
                "detail": f"MT5 closed {sym}"
                          + (f" — PnL={pnl_usd:+.2f}$" if pnl_usd is not None else ""),
            })

    out["positions"] = pg_positions
    out["closed_trades"] = closed
    out["equity"] = float(out.get("equity", 0) or 0)
    return out
