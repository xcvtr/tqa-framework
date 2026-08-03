"""Grid sweep — перебор параметров.

Читает конфиг стратегии + grid params → запускает Backtester для каждой комбинации.
"""

from __future__ import annotations

from typing import Optional
import itertools


class GridRunner:
    """Sweep параметров стратегии."""

    def __init__(self, strategy: str, params: dict,
                 tickers: Optional[list[str]] = None):
        self.strategy = strategy
        self.params = params  # {param: [values]}
        self.tickers = tickers or []

    def combinations(self) -> list[dict]:
        """Все комбинации параметров."""
        keys = list(self.params.keys())
        vals = list(self.params.values())
        combos = []
        for combo in itertools.product(*vals):
            combos.append(dict(zip(keys, combo)))
        return combos

    def run(self) -> list[dict]:
        """Запустить sweep. Возвращает результаты."""
        raise NotImplementedError
