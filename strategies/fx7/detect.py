"""FX-7 проект: детект — обёртка над M20 volume-profile кластерным детектом.

Обёртка над TQA-FX-TOP `engine/live_engine.generate_candidates` (который в свою
очередь зовёт `lifecycle.run` на скользящем окне now-3d → now). Приводит
кандидат-словари к контракту framework `exchange_base.Signal`.

Чистая функция: не пишет в БД, не трогает позиции — только детект. Тяжёлый
volume-profile детектор живёт в TQA-FX-TOP (прод не меняем), здесь — адаптация
входа/выхода. Путь к репо задаётся env `TQA_FX_TOP_PATH` (по умолчанию
/home/user/projects/TQA-FX-TOP).
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from tqa_framework.engine.exchange_base import Signal

# FX-7 направление ('BUY'/'SELL') <-> framework Signal ('LONG'/'SHORT').
FX7_TO_SIGNAL = {"BUY": "LONG", "SELL": "SHORT"}
SIGNAL_TO_FX7 = {"LONG": "BUY", "SHORT": "SELL"}

TQA_FX_TOP_DEFAULT = "/home/user/projects/TQA-FX-TOP"


def _live_engine():
    """Ленивый импорт TQA-FX-TOP live_engine (с добавлением пути в sys.path)."""
    root = os.environ.get("TQA_FX_TOP_PATH", TQA_FX_TOP_DEFAULT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import engine.live_engine  # noqa: PLC0415 — импорт из внешнего репо

    return engine.live_engine


def detect_params(config: Optional[dict]) -> dict:
    """Извлечь параметры детекта из YAML-конфига стратегии."""
    d = (config or {}).get("detect", {})
    return {
        "threshold": float(d.get("threshold", 1.5)),
        "atr_break_enabled": bool(d.get("atr_break_enabled", True)),
        "atr_k": float(d.get("atr_k", 0.5)),
        "atr_period": int(d.get("atr_period", 72)),
        "window_days": int(d.get("window_days", 3)),
    }


def to_signal(cand: dict) -> Signal:
    """Кандидат-словарь из lifecycle → framework Signal."""
    direction = FX7_TO_SIGNAL.get(cand.get("direction", ""), cand.get("direction", ""))
    return Signal(
        symbol=cand["sym"],
        direction=direction,
        price=float(cand["entry_price"]),
        timestamp=str(cand["entry_time"]),
        strategy="fx7",
        reason=f"cluster={cand.get('cluster_type', '')}@{cand.get('cluster_level', 0)}",
    )


def detect(symbols: list[str], now, config: Optional[dict] = None, ch=None) -> list[Signal]:
    """Детект кандидатов FX-7 → list[framework Signal].

    Обёртка над `generate_candidates`: для каждого символа из symbols в сессии
    запускает M20 volume-profile lifecycle на окне (now - window_days, now) и
    возвращает сырые кандидаты как Signals. Никаких side-эффектов.

    Args:
        symbols: список пар (lowercase), напр. ['euraud', 'eurgbp', ...].
        now: текущее время (datetime) — точка генерации.
        config: YAML-конфиг стратегии (блок 'detect').
        ch: опциональный CH-клиент (для переиспользования); иначе свой.
    """
    le = _live_engine()
    p = detect_params(config)
    cands = le.generate_candidates(
        symbols,
        now,
        ch=ch,
        threshold=p["threshold"],
        atr_break_enabled=p["atr_break_enabled"],
        atr_k=p["atr_k"],
        atr_period=p["atr_period"],
    )
    return [to_signal(c) for c in cands]
