# Checkpoint 008: LSR-CROSS портирован в YAML (1:1 с Python)

## Что сделано
- Добавлен action `lsr_execute` в `actions.py` — simulate_trade с 1m hi/lo сканированием
- Добавлен `_run_lsr_mode()` в `backtester.py` — pre-compute trades + 1m MTM portfolio loop
- Точка входа в `run()`: если YAML action=lsr_execute → LSR mode, иначе legacy per-tick
- Создан `strategies/lsr_cross/config.yaml` и записан в PG `shared.strategies`
- `--strategy-yaml lsr_cross` теперь использует YAML engine, а не Python LsrCrossBacktester

## Результат
- **+916.51%** ret / **18.92%** DD / **1319** trades / **Calmar 48.44**
- **1:1** с LsrCrossBacktester (risk=0.14, 3y, 9 tickers, no-excl)

## Файлы
- `tqa_framework/strategy_engine/actions.py` — +_lsr_execute (170 строк)
- `tqa_framework/engine/backtester.py` — +_run_lsr_mode() (225 строк) + точка входа (8 строк)
- `strategies/lsr_cross/config.yaml` — YAML конфиг (27 строк)
- PG: `shared.strategies.lsr_cross` обновлён