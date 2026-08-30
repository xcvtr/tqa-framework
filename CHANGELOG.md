# Changelog

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