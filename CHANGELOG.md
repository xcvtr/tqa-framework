# Changelog

## [003] 2026-08-27
### Added
- strategy_engine/ — модуль декларативных YAML-стратегий (models, parser, metrics, actions, runtime)
- tests/test_strategy_engine/ — 60 unit-тестов, 90% coverage
- strategies/synthetic_bond/config.yaml — конфиг synthetic bond стратегии
- CLI: `tqa strategy create/get/list` — управление стратегиями в PG
- scripts/seed_strategies.py — init-контейнер для загрузки YAML в PG
- docs/strategy-engine-spec.md — полный spec YAML-формата
### Changed
- engine/backtester.py — поддержка --strategy-yaml (PG-first, graceful degradation)
- engine/cli.py — subparser `strategy`, --strategy-yaml флаг
- engine/pg_state.py — CRUD для shared.strategies
- Checkpoint: checkpoint/003-strategy-engine-pg-storage.md

## [002] 2026-07-27
### Added
- engine/pg_state.py — реализован PG коннект через PG_URL env, CRUD
- engine/detect.py — load_m1_from_ch, resample_bars, dedup_signals
- engine/backtester.py — портфельный бэктестер (importlib загрузка стратегий)
- engine/cli.py — диспетчер команд (backtest/grid/paper/results)
- docker/compose.yml — тестовый PostgreSQL
- docker/pg.sh — утилита управления тестовым PG
- scripts/bt_report.py — equity curve + метрики → PNG → Matrix
- strategies/test_ma/ — тестовая стратегия MA crossover
- Checkpoint: checkpoints/001-tqa-framework-initial.md
