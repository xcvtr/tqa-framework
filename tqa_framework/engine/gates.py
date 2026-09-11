"""Гейты входа — единый модуль для live- и backtest-контура.

Обобщение TQA-FX-TOP `engine/live_gates.py` (12 предикатов) в чистые функции
без side-эффектов, параметризованные YAML-конфигом. Константы (ENTER dedup,
stale price, CL3, mom3d, NET growth), correlation-группы и per-symbol
направления берутся из `GateConfig`, а не хардкодятся.

ЕДИНЫЙ модуль: его импортируют и live-контур, и rolling-backtest, чем закрывается
дублирование инлайн-фильтров из TQA-FX-TOP `paper_trader.open_signals`.

Каждый предикат чистый: `(signal, ctx, config) -> (blocked: bool, reason: str|None)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover — yaml не обязателен без файлового конфига
    yaml = None


# ─────────────────────────────────────────────────────────────────────────────
# Конфиг гейтов (строится из YAML, не хардкод)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GateConfig:
    """Параметры гейтов, собираемые из YAML-конфига стратегии.

    Поля полностью замещают жёстко зашитые константы live_gates.py:
      - session:            {sym: (start_hour, end_hour)} торговая сессия (UTC)
      - enter_dedup_hours:  не эмитить повторно ENTER по паре чаще раза в сутки
      - stale_price_pct:    текущая цена не дальше N% от entry
      - cl3_n:              пауза после N убыточных подряд по паре
      - mom3d_threshold:    вход только при |моментуме за 3д| < N%
      - correlation_groups: {group: [syms]} — не 2+ позиции одного направления
      - dir_cfg:            {sym: 'BUY'|'SELL'} — per-symbol направление
      - sell_trend_filter:  {sym: 'sma72'|''} — запрет SELL в uptrend
      - net_growth_factor:  NET-фильтр: толпа должна СБРАСЫВАТЬ (net_now < net_prev*f)
    """

    session: dict[str, tuple[int, int]] = field(default_factory=dict)
    enter_dedup_hours: float = 24.0
    stale_price_pct: float = 0.3
    cl3_n: int = 3
    mom3d_threshold: float = 0.5
    correlation_groups: dict[str, list[str]] = field(default_factory=dict)
    dir_cfg: dict[str, str] = field(default_factory=dict)
    sell_trend_filter: dict[str, str] = field(default_factory=dict)
    net_growth_factor: float = 1.2

    # ── построение из YAML / dict ────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "GateConfig":
        if not data:
            return cls()
        g = data.get("gates", {})
        session = {}
        for sym_cfg in data.get("symbols", []) or []:
            sym = str(sym_cfg.get("sym", "")).lower()
            if not sym:
                continue
            sess = sym_cfg.get("session")
            if sess:
                session[sym] = (int(sess[0]), int(sess[1]))
        sell_trend = {}
        for sym_cfg in data.get("symbols", []) or []:
            sym = str(sym_cfg.get("sym", "")).lower()
            stf = sym_cfg.get("sell_trend_filter")
            if stf:
                sell_trend[sym] = stf
        return cls(
            session=session,
            enter_dedup_hours=float(g.get("enter_dedup_hours", 24.0)),
            stale_price_pct=float(g.get("stale_price_pct", 0.3)),
            cl3_n=int(g.get("cl3_n", 3)),
            mom3d_threshold=float(g.get("mom3d_threshold", 0.5)),
            correlation_groups=dict(g.get("correlation_groups", {}) or {}),
            dir_cfg=dict(g.get("dir_cfg", {}) or {}),
            sell_trend_filter=sell_trend,
            net_growth_factor=float(g.get("net_growth_factor", 1.2)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "GateConfig":
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML не установлен — нужен для from_yaml")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))

    def session_for(self, sym: str) -> tuple[int, int]:
        return self.session.get(sym.lower(), (0, 24))
@dataclass
class GateContext:
    """Окружение для оценки гейтов на текущем шаге (без side-эффектов)."""

    now: Optional[datetime] = None
    positions: list[dict] = field(default_factory=list)       # {'sym','direction',...}
    recent_enters: dict = field(default_factory=dict)          # {(sym, dir): label}
    pair_cl: dict = field(default_factory=dict)                # {sym: n}
    net: Optional[tuple[Optional[float], Optional[float]]] = None  # (net_now, net_prev)
    sma72: Optional[float] = None
    mom3d: Optional[float] = None
    price: Optional[float] = None
    last_trade: Optional[tuple[str, Optional[float]]] = None   # (direction, pnl)
    cluster_alive: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Предикаты (чистые, (sig, ctx, config) -> (blocked, reason))
# ─────────────────────────────────────────────────────────────────────────────
def is_in_session(sym: str, now: datetime, config: GateConfig) -> bool:
    """Входит ли время в торговую сессию символа (UTC)."""
    if now is None:
        return True
    h = now.hour
    s, e = config.session_for(sym)
    if s <= e:
        return s <= h < e
    return h >= s or h < e


def gate_session(sig, ctx: GateContext, config: GateConfig) -> tuple:
    if not is_in_session(sig["sym"], ctx.now, config):
        return True, "out-of-session"
    return False, None


def gate_enter_dedup(sig, ctx: GateContext, config: GateConfig) -> tuple:
    """recent_enters: {(sym, dir): last_label}. Не чаще раза в enter_dedup_hours."""
    key = (sig["sym"], sig["direction"])
    last = ctx.recent_enters.get(key)
    if last is None:
        return False, None
    return True, f"ENTER {sig['sym']} {sig['direction']} уже отправлен (dedup {config.enter_dedup_hours}h)"


def gate_consecutive_loss(sig, ctx: GateContext, config: GateConfig) -> tuple:
    n = ctx.pair_cl.get(sig["sym"], 0)
    if n >= config.cl3_n:
        return True, f"ConsecutiveLoss {n}>= {config.cl3_n}"
    return False, None


def gate_correlation(sig, ctx: GateContext, config: GateConfig) -> tuple:
    d = sig["direction"]
    sym = sig["sym"].lower()
    for group, syms in config.correlation_groups.items():
        if sym in syms:
            same_dir = sum(
                1 for p in ctx.positions
                if p["direction"] == d and str(p["sym"]).lower() in syms
            )
            if same_dir >= 2:
                return True, f"correlation {group}: {same_dir} same-dir"
    return False, None


def gate_stale_price(sig, ctx: GateContext, config: GateConfig) -> tuple:
    price = ctx.price
    entry = float(sig.get("entry_price", 0))
    if price is None:
        return True, "no-price"
    diff_pct = abs(price - entry) / entry * 100 if entry else 0.0
    if diff_pct > config.stale_price_pct:
        return True, f"stale price: diff={diff_pct:.2f}%"
    return False, None


def gate_sma72_sell(sig, ctx: GateContext, config: GateConfig) -> tuple:
    if sig["direction"] != "SELL":
        return False, None
    stf = config.sell_trend_filter.get(sig["sym"], "")
    if stf == "sma72" and ctx.sma72 is not None and ctx.price is not None:
        if ctx.price > ctx.sma72:
            return True, f"price {ctx.price:.4f} > SMA72 {ctx.sma72:.4f} (uptrend)"
    return False, None


def gate_dir_cfg(sig, ctx: GateContext, config: GateConfig) -> tuple:
    want = config.dir_cfg.get(sig["sym"])
    if want and sig["direction"] != want:
        return True, f"направление {want}-only (чекп.090)"
    return False, None


def gate_mom3d(sig, ctx: GateContext, config: GateConfig) -> tuple:
    mom3d = ctx.mom3d
    if mom3d is not None and mom3d >= config.mom3d_threshold:
        return True, f"mom3d={mom3d:+.2f}% >= {config.mom3d_threshold}% (чекп.090)"
    return False, None


def gate_net(sig, ctx: GateContext, config: GateConfig) -> tuple:
    """NET-фильтр: вход только когда толпа СБРАСЫВАЕТ (net_now < net_prev*f)."""
    if ctx.net is None:
        return True, "net unavailable (fail-closed)"  # R10: fail-closed при сбое CH
    net_now, net_prev = ctx.net
    if net_prev and net_prev > 0 and net_now >= net_prev * config.net_growth_factor:
        return True, "NET набирает — толпа не сбрасывает"
    return False, None


def gate_cluster_alive(sig, ctx: GateContext, config: GateConfig) -> tuple:
    if not ctx.cluster_alive:
        return True, "cluster gone"
    return False, None


def gate_last_profit_opposite(sig, ctx: GateContext, config: GateConfig) -> tuple:
    if ctx.last_trade is None:
        return False, None
    ld, lpnl = ctx.last_trade
    if lpnl and lpnl > 0 and ld != sig["direction"]:
        return True, f"last {ld} +{lpnl:.2f} против {sig['direction']}"
    return False, None


# Порядок = порядок применения в live open_signals.
DEFAULT_GATES = [
    gate_session,
    gate_enter_dedup,
    gate_consecutive_loss,
    gate_correlation,
    gate_stale_price,
    gate_sma72_sell,
    gate_dir_cfg,
    gate_mom3d,
    gate_net,
    gate_cluster_alive,
    gate_last_profit_opposite,
]


def apply_gates(sig, ctx: GateContext, config: GateConfig,
                gates: Optional[list] = None) -> tuple:
    """Прогнать сигнал через все гейты. Возвращает (blocked, reason)."""
    gates = gates or DEFAULT_GATES
    for g in gates:
        blocked, reason = g(sig, ctx, config)
        if blocked:
            return True, reason
    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# Помощники совместимости с framework Signal
# ─────────────────────────────────────────────────────────────────────────────
def sig_from_signal(signal, direction_map=None) -> dict:
    """Framework Signal (dataclass) → dict-сигнал для гейтов.

    direction_map: {'LONG': 'BUY', 'SHORT': 'SELL'} для перевода LONG/SHORT → BUY/SELL
    (framework Signal использует LONG/SHORT, FX-7 гейты — BUY/SELL).
    """
    direction = signal.direction
    if direction_map and direction in direction_map:
        direction = direction_map[direction]
    return {
        "sym": signal.symbol,
        "direction": direction,
        "entry_price": float(signal.price),
        "entry_time": signal.timestamp,
    }
