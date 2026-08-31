#!/usr/bin/env python3
"""Загрузить YAML-стратегии из проекта в PG.

Стратегии принадлежат проектам (не tqa-framework). Утилита ищет .yaml
в указанной директории проекта и загружает каждый в PG `shared.strategies`.

Использование:
    python scripts/seed_strategies.py --dir ~/projects/TQA-crypto/strategies \
        [--pg-url URL]

Имя стратегии берётся из поля 'strategy' в YAML (обязателен).
Если файл невалидный — ошибка, но остальные файлы продолжают загружаться.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed")


def main():
    parser = argparse.ArgumentParser(description="Seed strategies from project YAML files to PG")
    parser.add_argument("--pg-url", help="PG URL (default: из PG_URL env)")
    parser.add_argument("--dir", default="",
                        help="Директория проекта с YAML-стратегиями (например ~/projects/<proj>/strategies)")
    args = parser.parse_args()

    if not args.dir:
        logger.error("Укажите --dir с путём к директории проекта (стратегии живут в проектах, не в пакете). "
                     "Пример: python scripts/seed_strategies.py --dir ~/projects/TQA-crypto/strategies")
        sys.exit(1)

    from tqa_framework.engine.pg_state import PGState
    from tqa_framework.strategy_engine.parser import load_strategy_from_string

    pg = PGState(pg_url=args.pg_url)
    pg.ensure_tables_strategies()

    strategy_dir = Path(args.dir).expanduser()
    if not strategy_dir.is_dir():
        logger.error("Директория не найдена: %s", strategy_dir)
        sys.exit(1)

    yaml_files = sorted(strategy_dir.rglob("*.yaml"))
    if not yaml_files:
        logger.warning("Нет .yaml файлов в %s", strategy_dir)
        return

    loaded = 0
    errors = 0
    for f in yaml_files:
        try:
            yaml_str = f.read_text()
            strategy = load_strategy_from_string(yaml_str)
            pg.save_strategy(strategy.name, yaml_str, version=strategy.version)
            logger.info("✓ %s → %s (v%s)", f.name, strategy.name, strategy.version)
            loaded += 1
        except Exception as e:
            logger.error("✗ %s: %s", f, e)
            errors += 1

    logger.info("Загружено: %d, ошибок: %d", loaded, errors)


if __name__ == "__main__":
    main()