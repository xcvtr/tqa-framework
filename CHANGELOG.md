# Changelog

## [006] 2026-08-31
### Fixed
- 10× расхождение CLI vs Python API: неполные params в walk-forward скрипте (без trail_act/trail_dist/trail_lock/sl_pct/tp_pct)
- API LsrCrossBacktester теперь 1:1 с CLI при полных params
### Changed
- Walk-forward no-excl выживает OOS: Train +87.4%, Test +132.0%
- full 3y no-excl: +287.74%/DD 11.22%/1319t/Calmar 25.63
- Checkpoint: checkpoints/006-lsr-cross-10x-fix-walkforward.md

## [005] 2026-08-30
### Changed
- LSR-CROSS YAML: excludes disabled (hours=[], dow=[], syms=[]) — no-excludes конфигурация
- CLI-верификация: z=1.5 не стоит (+10% ROI за +55% DD)
- Лучшая конфигурация: no-excludes + risk=0.12 → +640.8%/DD 16.42%/Calmar 39.0
### Added
- /tmp/walk_forward.py — walk-forward скрипт через Python API LsrCrossBacktester

## [004] 2026-08-30
### Added
- YAML bricks: hold timeout, pyramiding trigger/add, risk section loading in universal Backtester
- `--strategy-yaml lsr_cross` auto-routing → `LsrCrossBacktester` (event-driven 1m hi/lo sim)
- YAML params merge with `--params` (params override YAML defaults)
- Checkpoint: checkpoints/004-yaml-bricks-lsr-cross.md
### Fixed
- `detect.py`: toTimeZone for bar timestamp sync (vs toDateTime64 that shifted +3h)
- `backtester.py`: close params loading from PG YAML, exclude filters from PG YAML

## [003] 2026-08-30
### Added
- Strategy Engine PG storage: save/load/list YAML strategies in `shared.strategies`
- Strategy management CLI: `tqa strategy create/get/list`

## [002] 2026-08-29
### Fixed
- Backtester: portfolio MTM equity, MDD calculation fix
- Backtester: resample_bars dedup, roll_gap handling

## [001] 2026-08-29
### Added
- Initial tqa-framework: universal Backtester, Strategy Engine, CLI, PG storage