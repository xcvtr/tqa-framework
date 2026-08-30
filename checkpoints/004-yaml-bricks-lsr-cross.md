---
title: "YAML bricks: hold timeout + pyramiding + risk section"
checkpoint: 4
date: 2026-08-30
tags: [checkpoint, tqa-framework, yaml, lsr_cross]
---

# YAML bricks: hold timeout + pyramiding + risk section

Добавлены 3 недостающих кирпичика в универсальный `Backtester` для поддержки event-driven стратегий через YAML.

## Изменения

### `tqa_framework/engine/backtester.py`

**Brick 1: Risk section loading** (lines ~179-218)
- Загрузка `risk.pyramiding.trigger/add_multiplier` → `strategy_params['pyramiding_trigger']`, `strategy_params['pyramiding_add_multiplier']`
- Загрузка `risk.exit_timeout_hours` → `strategy_params['exit_timeout_hours']`
- Реализована в ОБОИХ ветках загрузки (PG и файловый путь)

**Brick 2: Hold timeout** (lines ~489-499)
- Отслеживание `bars_held` для каждой позиции в per-bar цикле
- При `bars_held * tf_minutes >= exit_timeout_hours * 60` — закрытие с reason `"timeout"`
- Edge case: позиция открытая на текущем баре начинает счёт с 0
- Когда `exit_timeout_hours = 0` — таймаут не применяется (обратная совместимость)

**Brick 3: Pyramiding trigger/add** (lines ~644, 660-675)
- В `_open_position()` добавлен параметр `positions: Optional[list] = None`
- При открытии позиции проверяет существующие открытые позиции на том же символе/направлении
- Если unrealized PnL% >= `pyramiding_trigger` — риск умножается на `(1 + pyramiding_add_multiplier * pyra_level)`
- Когда `pyramiding_trigger = 0` — пирамидинг не применяется (обратная совместимость)

**Также:** исправления из предыдущих сессий
- `detect.py`: `toTimeZone` для синхронизации таймзоны баров (вместо неверного toDateTime64)
- `backtester.py`: загрузка `close` params из PG + exclude фильтры из YAML

### `tqa_framework/engine/detect.py`
- Исправлен `toTimeZone(timestamp, 'UTC')` вместо `toDateTime64(toString(timestamp),3,'UTC')`

## Результаты теста (ETHUSDT, 365d, risk=0.08, equity=1000)

```
┌─────────────────────┬──────────┬──────────┬───────────┐
│ Метрика             │ YAML до  │ YAML после│ Python    │
├─────────────────────┼──────────┼──────────┼───────────┤
│ Доходность          │ -2.87%   │ +47.17%  │ +10.65%   │
│ MDD                 │ 62.26%   │ 50.40%   │ 5.89%     │
│ Win Rate            │ 78.4%    │ 80.7%    │ 57.3%     │
│ Profit Factor       │ 0.98     │ 1.40     │ 1.61      │
│ Сделок              │ 51       │ 57       │ 117       │
│ Calmar              │ -0.05    │ 0.94     │ 1.81      │
└─────────────────────┴──────────┴──────────┴───────────┘
```

YAML догнал по доходности, но DD в 8× выше и сделок в 2× меньше — разница в архитектуре симуляции (bar-driven vs event-driven с 1m hi/lo).

## Различия между YAML и Python

| Аспект               | YAML (универсальный Backtester) | Python (LsrCrossBacktester)    |
|----------------------|--------------------------------|-------------------------------|
| Entry                | На TF-баре с совпадением метки | На **следующем** 5m баре      |
| Exit sim             | Close TF-бара                  | 1m hi/lo (точное касание SL)  |
| Event loop           | Per-tick (все бары)            | Per-event (bounded scan)      |
| Pyramiding           | Через unrealized PnL           | Через base_risk * multiplier  |
| Hold timeout         | bars_held * tf_minutes         | timedelta от entry_time       |

## Состояние для продолжения

1. **Нужен 4-й кирпич**: обёртка `--strategy-yaml lsr_cross` → `LsrCrossBacktester` с параметрами из YAML
2. Либо: рефакторинг универсального Backtester для поддержки 1m sub-bar симуляции при наличии внешних сигналов
3. LSR-CROSS YAML в PG: имя `lsr_cross`, загружен через `save_strategy('lsr_cross', yaml_content)`

## Изменённые файлы
- `tqa_framework/engine/backtester.py`
- `tqa_framework/engine/detect.py`
- `tqa_framework/engine/cli.py`
- `tqa_framework/engine/pg_state.py`
- `tqa_framework/grid/runner.py`
- `tqa_framework/strategy_engine/__init__.py`
- `tqa_framework/strategy_engine/metrics.py`
- `tqa_framework/strategy_engine/models.py`
- `tqa_framework/strategy_engine/parser.py`
- `tqa_framework/strategy_engine/runtime.py`