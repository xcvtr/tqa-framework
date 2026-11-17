"""Тонкая обёртка: live/paper/backtest — ОДИН движок (self_tester).

Исторически здесь жил второй бэктест со своими сайзингом и параметрами
(pyramid 0.3/0.01/2, go_knur 1.0) — он расходился с каноном и не использовался.
Теперь модуль только пробрасывает аргументы, чтобы не было второй реализации.

  python -m strategies.trend_sma.runner --tickers MN --start 2026-01-01 --end 2026-11-11
"""
from __future__ import annotations

import sys

from strategies.trend_sma.self_tester import main as _self_tester_main


def main(argv: list[str] | None = None) -> int:
    if argv is not None:
        sys.argv = ['self_tester'] + list(argv)
    return _self_tester_main()


if __name__ == '__main__':
    sys.exit(main() or 0)
