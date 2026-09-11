"""FX-7 проект: единый входной контракт детект + гейты.

- `fx7_detect_all(symbols, now, gates_ctx)` — один шаг как live open:
  детект через `detect()` + прогон через engine.gates. Используется и live-контуром,
  и rolling-backtest (по образцу TQA-FX-TOP `live_engine.generate_candidates`).
- `fx7_detect_all_rolling(...)` — повторяет 20-мин цикл для бэктеста
  (по образцу `generate_candidates_rolling`): честная плотность входов live=backtest.

Состояние кластеров персистентное (PG multi_state.clusters, см. cluster_state.py),
поэтому rolling-цикл и live видят одинаковое состояние между циклами (риск R3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from tqa_framework.engine.exchange_base import Signal
from tqa_framework.engine.gates import (
    GateConfig,
    GateContext,
    apply_gates,
    sig_from_signal,
)

from strategies.fx7.detect import SIGNAL_TO_FX7, detect

STEP_MINUTES = 20  # скользящая перегенерация (как live open каждые 20 мин)


@dataclass
class GatesContext:
    """Контекст гейтов одного шага: конфиг + окружение + сырой YAML (для детекта)."""

    config: GateConfig = field(default_factory=GateConfig)
    ctx: GateContext = field(default_factory=GateContext)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str, now: Optional[datetime] = None) -> "GatesContext":
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(
            config=GateConfig.from_dict(raw),
            ctx=GateContext(now=now),
            raw=raw,
        )


def fx7_detect_all(symbols: list[str], now, gates_ctx: GatesContext, ch=None) -> list[Signal]:
    """Детект + гейты за один шаг → список прошедших Signals.

    Единый входной контракт (live и backtest зовут одно и то же).
    Сигналы, заблокированные любым гейтом, отбрасываются.
    """
    signals = detect(symbols, now, config=gates_ctx.raw, ch=ch)
    passed = []
    for sig in signals:
        gsig = sig_from_signal(sig, direction_map=SIGNAL_TO_FX7)
        blocked, _reason = apply_gates(gsig, gates_ctx.ctx, gates_ctx.config)
        if not blocked:
            passed.append(sig)
    return passed


def fx7_detect_all_rolling(symbols: list[str], start_dt, end_dt, gates_ctx: GatesContext,
                           step_minutes: int = STEP_MINUTES, ch=None) -> list[Signal]:
    """Backtest-симулятор: повторяет скользящий live-цикл open.

    На каждом 20-мин шаге от start_dt до end_dt вызывает fx7_detect_all
    (скользящее окно шаг-3d → шаг) и собирает уникальные входы
    (ENTER-dedup по (sym, dir) с горизонтом enter_dedup_hours, как live).

    gates_ctx.ctx обновляется вызывающим между шагами (позиции/CL3/NET/цены на
    исторический момент); здесь поддерживается только ENTER-24h dedup.
    """
    step = timedelta(minutes=step_minutes)
    all_entries: list[Signal] = []
    seen: dict[tuple[str, str], str] = {}  # (sym, dir) -> last entry_time
    dedup_hours = gates_ctx.config.enter_dedup_hours
    t = start_dt
    while t <= end_dt:
        for sig in fx7_detect_all(symbols, t, gates_ctx, ch=ch):
            key = (sig.symbol, sig.direction)
            et = str(sig.timestamp)[:16]
            last = seen.get(key)
            if last is not None:
                try:
                    ldt = datetime.fromisoformat(last)
                    tdt = datetime.fromisoformat(et)
                    if (tdt - ldt) < timedelta(hours=dedup_hours):
                        continue
                except ValueError:
                    pass
            seen[key] = et
            all_entries.append(sig)
        t += step
    return all_entries