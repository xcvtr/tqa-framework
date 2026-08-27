---
title: "Strategy Engine: YAML-декларативные стратегии + PG storage"
checkpoint: 3
date: 2026-08-27
tags: [checkpoint, tqa-framework, strategy-engine, yaml, pg]
---

# Checkpoint 003: Strategy Engine + PG Storage

## Что сделано

Добавлен декларативный движок стратегий (YAML вместо Python detect/tick) + хранилище стратегий в PostgreSQL (fault tolerance / контейнеризация).

### 1. Spec и формат

- `docs/strategy-engine-spec.md` — полный spec YAML-формата: metrics, entry_rules/exit_rules, AND/OR группы, first-match по priority, sweep, шаблонизация `{{param}}`, схема PG.

### 2. Модуль `tqa_framework/strategy_engine/`

```
strategy_engine/
├── __init__.py   # публичный API: load_strategy, evaluate, register_metric/action, ...
├── models.py     # StrategyDef, SignalConfig, ConditionGroup, MetricDef, Signal
├── parser.py     # YAML → StrategyDef (валидация метрик/экшенов/групп)
├── metrics.py    # реестр метрик: close, high, low, volume, price, zscore, sma, hour, session, pos_num, bars_held, basis (12 шт)
├── actions.py    # реестр экшенов: open_long/short, close_long/short/all, noop (6 шт)
└── runtime.py    # evaluate(bars, strategy, state) → list[Signal]; first-match; AND/OR
```

- 60 unit-тестов, 90% coverage (`tests/test_strategy_engine/`)
- Метрики: простые (close/high/low/volume/price), тайм (hour/session), статистика (zscore/sma), позиционные (pos_num/bars_held), basis (fut/lot/spot - 1)
- Грациозный fаllback: если YAML не загружается — старое поведение importlib detect/tick

### 3. PG storage стратегий (контейнеризация)

Схема `shared.strategies` — YAML как единственный источник правды, один TEXT, без JSONB:

```sql
CREATE TABLE shared.strategies (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '2.0',
    yaml TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

CLI: `tqa strategy create/get/list` (валидация YAML перед сохранением).

### 4. Интеграция в backtester/CLI

- `--strategy-yaml <path>` — грузит YAML через strategy_engine (graceful degradation: не загрузилось → importlib detect/tick)
- `--strategy-yaml <name>` — ищет сначала в PG (`shared.strategies`), если нет — как файл
- `scripts/seed_strategies.py` — init-контейнер: залить все `.yaml` из директории в PG

### 5. Synthetic bond конфиг

`tqa_framework/strategies/synthetic_bond/config.yaml` — 4 сигнала (entry_long/entry_short, exit_long/exit_short), basis_zscore вход ±2, exit по реверсии/таймауту 10 баров.

## Проверено

```
# unit-тесты
python -m pytest tests/test_strategy_engine/ -v  → 60 passed

# импорт пакета
python -c "from tqa_framework.strategy_engine import *" → OK (12 метрик, 6 экшенов)

# CLI management
tqa strategy create synthetic_bond .../config.yaml → Стратегия v2.0 сохранена в PG
tqa strategy list                                   → synthetic_bond  2.0
tqa strategy get synthetic_bond                     → (выводит YAML)

# backtest через PG storage
tqa backtest --tickers Si --strategy-yaml synthetic_bond --days 30 --tf 60
  → "Strategy Engine загружен из PG: synthetic_bond"
  → CH последний бар, загрузка Si, 316 баров на TF=60
```

## Состояние для продолжения

- [ ] Прод-схема `backtest.summary` не обновлена (нет колонки strategy) — бэктест падает на сохранении `save_summary`. Нужно: ALTER TABLE или пересоздать схему.
- [ ] `tqa strategy update/delete` — пока только create/get/list
- [ ] `grid --strategy-yaml` — sweep параметров из YAML-секции ещё не проверен
- [ ] Docker: init-контейнер (`seed_strategies.py`) → app читает из PG; если PG нет — graceful degradation на файл

## Изменённые файлы

```
docs/strategy-engine-spec.md                      (новый, spec)
scripts/seed_strategies.py                        (новый, init-контейнер)
tqa_framework/strategy_engine/                    (новый, 6 файлов)
tqa_framework/strategies/synthetic_bond/          (новый, config.yaml + configs/default.yaml)
tqa_framework/engine/backtester.py                (интеграция strategy_engine, PG-first)
tqa_framework/engine/cli.py                       (subparser strategy, --strategy-yaml)
tqa_framework/engine/pg_state.py                  (shared.strategies CRUD)
tests/test_strategy_engine/                       (новый, 60 тестов)
```