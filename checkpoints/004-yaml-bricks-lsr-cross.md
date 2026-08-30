---
title: "YAML bricks + routing LSR-CROSS to LsrCrossBacktester"
checkpoint: 4
date: 2026-08-30
tags: [checkpoint, tqa-framework, yaml, lsr_cross]
---

# YAML bricks + routing `--strategy-yaml lsr_cross` → `LsrCrossBacktester`

## Изменения

### 4-й кирпич: YAML routing в `cli.py`
- `--strategy-yaml lsr_cross` автоматом роутится на `LsrCrossBacktester` (event-driven с 1m hi/lo симуляцией)
- Close params (sl, tp, trail) и risk params (hold_h, pyramiding) загружаются из YAML и мержатся с `--params` (приоритет `--params`)
- `--params` пробивает YAML-дефолты — подтверждено тестом (trail_act=0.5 → 6.55% vs 10.71%)

### Brick 1-3: `backtester.py`
- **Risk section loading** (lines ~179-218): pyramiding trigger/add_multiplier, exit_timeout_hours
- **Hold timeout** (lines ~489-499): bars_held tracking, close with reason "timeout"
- **Pyramiding trigger/add** (lines ~644, 660-675): through unrealized PnL

### `detect.py`
- toTimeZone fix for bar timestamp sync

## Результаты

### ETHUSDT 365d (risk=0.08, eq=1000)
```
┌──────────────────┬───────────┬──────────┬───────────┐
│ Метрика          │ YAML 3 br │ 4th br   │ Python    │
├──────────────────┼───────────┼──────────┼───────────┤
│ Доходность       │ +47.17%   │ +10.71%  │ +10.65%   │
│ MDD              │ 50.40%    │ 5.89%    │ 5.89%     │
│ Win Rate         │ 80.7%     │ 59.0%    │ 57.3%     │
│ Profit Factor    │ 1.40      │ 1.62     │ 1.61      │
│ Сделок           │ 57        │ 117      │ 117       │
│ Calmar           │ 0.94      │ 1.82     │ 1.81      │
└──────────────────┴───────────┴──────────┴───────────┘
```
После 4-го кирпича YAML-рутинг 1:1 с Python (10.71% vs 10.65% — микро-расхождение из-за CH-кэша).

### 10 тикеров, 1095d (risk=0.08, eq=1000, max_conc=6)
```
┌──────────────────┬──────────────────┐
│ Метрика          │ YAML-routed      │
├──────────────────┼──────────────────┤
│ Доходность       │ +238.64%         │
│ MDD              │ 11.85%           │
│ Win Rate         │ 58.8%            │
│ Profit Factor    │ 1.71             │
│ Сделок           │ 1,173            │
│ Calmar           │ 20.14            │
└──────────────────┴──────────────────┘
```
SOLUSDT не вошёл (нет в `SYM_RISK` LsrCrossBacktester? — проверить).

## Состояние для продолжения

1. **Свип/гонка YAML-версии** на 11 тикерах с optuna через канбан t_afcd3278
2. Обновить YAML-конфиг lsr_cross в PG с оптимальными params
3. Проверить SOLUSDT

## Изменённые файлы
- `tqa_framework/engine/cli.py` — YAML routing + params merge
- `tqa_framework/engine/backtester.py` — 3 bricks
- `tqa_framework/engine/detect.py` — toTimeZone fix
- `tqa_framework/engine/pg_state.py`
- `tqa_framework/grid/runner.py`
- `tqa_framework/strategy_engine/__init__.py`
- `tqa_framework/strategy_engine/metrics.py`
- `tqa_framework/strategy_engine/models.py`
- `tqa_framework/strategy_engine/parser.py`
- `tqa_framework/strategy_engine/runtime.py`