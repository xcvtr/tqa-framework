# Changelog

## [001] 2026-07-27
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
