# Strategy Engine — YAML Declarative Format

**Spec v1 — 2026-08-27**

## Мотивация

Текущие стратегии в tqa-framework — Python-функции (`detect.py` / `tick.py`).
Каждая стратегия пишет свою логику в коде. Это ведёт к:

- Дублированию: MA crossover, z-score, basis — переписываются в каждом `detect.py`
- Смешению логики входа и выхода
- Отсутствию grid search без правки кода
- Невозможности declarative конфигурации (как Excavator Strategies.ini)

**Решение** — декларативный YAML-формат, описывающий стратегию целиком:
метрики, условия, экшены, группы, типизированные параметры.

## Архитектура

```
                   .strategy.yaml
                        │
                ┌───────┴────────┐
          load/validate       evaluate
                │                 │
                ▼                 ▼
        StrategyEngine      ┌─────┴─────┐
        (validator)       entry_rules  exit_rules
                                 │            │
                          conditions     conditions
                          (AND/OR)       (AND/OR)
                                 │            │
                           ┌─────┘            └─────┐
                        action(...)           action(...)
```

- **Engine** — единый executor, загружает `.strategy.yaml`, парсит, валидирует, исполняет
- **Metrics** — вычисляемые колонки на баре (basis, zscore, sma, atr, ...)
- **Rules** — first-match по priority, каждая → action
- **Entry + Exit** — равноправные, в одном файле

## Формат

```yaml
# meta — метаданные
name: my_strategy
version: 1
description: "MA crossover / synthetic bond basis"

# params — типизированные параметры стратегии
# Пользователь задаёт значения; engine подставляет их в метрики с {{param}}
params:
  fast_period:
    type: int
    default: 5
    min: 2
    max: 100
    description: "Период быстрой MA"
  slow_period:
    type: float
    default: 20.0
    min: 5.0
    max: 200.0
  threshold:
    type: float
    default: 0.002
  risk_pct:
    type: float
    default: 0.02
  sl_pct:
    type: float
    default: 0.02
  tp_pct:
    type: float
    default: 0.05

# metrics — вычисляемые метрики на баре
# Формула: выражение над OHLCV бара + другие метрики
# Доступны: open, high, low, close, volume, {{param}}
# Функции: sma(field, period), zscore(field, period), atr(period),
#          ema(field, period), std(field, period), highest(field, period),
#          lowest(field, period), crossover(a, b), crossunder(a, b)
metrics:
  fast_ma:
    formula: sma(close, {{fast_period}})
  slow_ma:
    formula: sma(close, {{slow_period}})

# entry_rules — правила входа
# first-match: первое правило с истинными условиями → action
entry_rules:
  - priority: 10
    action: BUY_LONG
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:                              # AND-группа
        - metric: fast_ma
          op: ">"
          value: slow_ma
        - metric: fast_ma
          op: crossunder
          value: slow_ma
          lookback: 1                    # проверить предыдущий бар
  - priority: 20
    action: SELL_SHORT
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:
        - metric: fast_ma
          op: "<"
          value: slow_ma
        - metric: fast_ma
          op: crossover
          value: slow_ma
          lookback: 1

# exit_rules — правила выхода
# Аналогичная структура: first-match на M1-тике
exit_rules:
  - priority: 10
    action: CLOSE_LONG
    conditions:
      any:                              # OR-группа
        - metric: pnl_pct
          op: "<="
          value: "{{sl_pct}}"
          source: position              # метрика из позиции, не из бара
        - metric: tie_period            # время удержания
          op: ">="
          value: 72                     # 72 бара = 3 дня на 5m TF
          source: position
```

## Типы параметров

| Type | Пример | Семантика |
|:-----|:-------|:----------|
| `int` | `5` | Целое |
| `float` | `0.02` | Число с плавающей |
| `range` | `[0.01, 0.05, 0.01]` | start, stop, step (для sweep) |
| `interval` | `[5, 20]` | [min, max] для grid |

## Действия (actions)

| Action | Параметры | Семантика |
|:-------|:----------|:----------|
| `BUY_LONG` | quantity, sl, tp | Открыть LONG |
| `SELL_SHORT` | quantity, sl, tp | Открыть SHORT |
| `CLOSE_LONG` | — | Закрыть все LONG |
| `CLOSE_SHORT` | — | Закрыть все SHORT |
| `CLOSE_ALL` | — | Закрыть всё |
| `REVERSE_LONG` | quantity, sl, tp | Переворот → LONG |
| `REVERSE_SHORT` | quantity, sl, tp | Переворот → SHORT |
| `NOOP` | — | Ничего (для логирования) |

## Источники метрик

| source | Доступные метрики |
|:-------|:-----------------|
| `bar` (default) | open, high, low, close, volume, все вычисляемые |
| `position` | entry_price, current_price, pnl, pnl_pct, bars_held |
| `market` | spread, day_net, oi_change, dom_imbalance |

## Операторы сравнения

| op | Семантика |
|:---|:----------|
| `>` / `>=` / `<` / `<=` / `==` / `!=` | Стандартные |
| `crosses_above` | Пересечение снизу вверх (текущий > value И предыдущий ≤ value) |
| `crosses_below` | Пересечение сверху вниз |
| `between` | min < metric < max (value задаётся строкой "lo, hi") |
| `inside` | threshold % внутри диапазона (value = [lo, hi, pct]) |
| `not_between` | metric ≤ lo ИЛИ metric ≥ hi |

## Вложенные группы

Поддерживаются вложенные `all` (AND) и `any` (OR) на любом уровне:

```yaml
conditions:
  all:
    - metric: session
      op: "=="
      value: "USA"
    - any:
        - all:
            - metric: fast_ma
              op: ">"
              value: slow_ma
            - metric: volume
              op: ">"
              value: avg_volume_20
              lookback: 20
        - all:
            - metric: zscore_basis
              op: ">"
              value: 2.0
            - metric: basis
              op: ">"
              value: 0
```

## Sweep (grid search)

In-file sweep секция — engine генерирует N комбинаций без правки файла:

```yaml
sweep:
  enabled: true
  params:
    threshold: [0.001, 0.002, 0.005, 0.01]    # явный список
    fast_period:
      range: [2, 15, 1]                         # start, stop, step
    slow_period:
      interval: [10, 50]                         # [min, max] → 5 points
  max_combinations: 100
```

## Шаблонизация

`{{param}}` — подстановка из `params` + из runtime контекста:

| Переменная | Источник |
|:-----------|:---------|
| `{{symbol}}` | Runtime (--tickers) |
| `{{risk_pct}}` | Runtime config |
| `{{tf}}` | Runtime (таймфрейм) |
| `{{fast_period}}` | Из params.yaml |

## Валидация

При загрузке `.strategy.yaml` через `StrategyEngine.load(path)`:

1. **Синтаксис** — YAML parse
2. **Параметры** — типы, границы, обязательность
3. **Метрики** — все ссылки (`{{param}}`) соответствуют params; встроенные функции существуют
4. **Действия** — action из допустимого списка
5. **Условия** — metric в metrics или built-in (close/open/high/low/volume)
6. **Циклы** — нет самоссылок в метриках (A → B → A)

Ошибка валидации = exit с сообщением. Невалидная стратегия не запускается.

## Пример файла: `ma_crossover.strategy.yaml`

```yaml
name: ma_crossover
version: 1
description: "Простой MA crossover — LONG когда fast > slow, SHORT когда fast < slow"

params:
  fast_period:
    type: int
    default: 5
    min: 2
    max: 100
  slow_period:
    type: int
    default: 20
    min: 5
    max: 200
  sl_pct:
    type: float
    default: 0.02
  tp_pct:
    type: float
    default: 0.05
  risk_pct:
    type: float
    default: 0.02

metrics:
  fast_ma:
    formula: sma(close, {{fast_period}})
  slow_ma:
    formula: sma(close, {{slow_period}})

entry_rules:
  - priority: 10
    action: BUY_LONG
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:
        - metric: fast_ma
          op: ">"
          value: slow_ma
        - metric: fast_ma
          op: crosses_above
          value: slow_ma
          lookback: 1

  - priority: 20
    action: SELL_SHORT
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:
        - metric: fast_ma
          op: "<"
          value: slow_ma
        - metric: fast_ma
          op: crosses_below
          value: slow_ma
          lookback: 1

exit_rules:
  - priority: 10
    action: CLOSE_LONG
    conditions:
      any:
        - metric: pnl_pct
          op: "<="
          value: "{{sl_pct}}"
          source: position
        - metric: pnl_pct
          op: ">="
          value: "{{tp_pct}}"
          source: position

  - priority: 20
    action: CLOSE_SHORT
    conditions:
      any:
        - metric: pnl_pct
          op: "<="
          value: "{{sl_pct}}"
          source: position
        - metric: pnl_pct
          op: ">="
          value: "{{tp_pct}}"
          source: position
```

Эквивалентный Python (нынешний test_ma detect.py + test_ma tick.py) — 75 строк кода.
YAML — 120 строк, но без единой строчки Python и с grid-ready sweep.

## Пример файла: `synthetic_bond_basis.strategy.yaml`

```yaml
name: synthetic_bond_basis
version: 1
description: "Synthetic bond: LONG basis when zscore(basis, 20d) < -2, SHORT when > +2"

params:
  lookback_days:
    type: int
    default: 20
    min: 5
    max: 100
  z_entry:
    type: float
    default: 2.0
    description: "Z-score порог входа"
  z_exit:
    type: float
    default: 0.5
    description: "Z-score порог выхода (отыгрыш)"
  sl_pct:
    type: float
    default: 0.03
  tp_pct:
    type: float
    default: 0.10
  risk_pct:
    type: float
    default: 0.03

# Вычисляемые метрики — строятся как колонки после ресемпла
metrics:
  # basis = close_fut / close_spot - 1
  basis:
    formula: (close / {{symbol}}_spot) - 1
    depends: [close]

  # zscore за N дней (≈ 24*{{lookback_days}} баров на 60m TF)
  zscore_basis:
    formula: zscore(basis, {{lookback_days}} * 24)
    depends: [basis]

  # SMA для среднего возврата (отыгрыш)
  sma_basis:
    formula: sma(basis, {{lookback_days}} * 24)

entry_rules:
  - priority: 10
    action: BUY_LONG
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:
        - metric: zscore_basis
          op: "<"
          value: "-{{z_entry}}"
        - metric: basis
          op: ">"
          value: 0
        - metric: close
          op: crosses_above
          value: sma_basis
          lookback: 2

  - priority: 20
    action: SELL_SHORT
    params:
      quantity: "{{risk_pct}}"
      sl: "{{sl_pct}}"
      tp: "{{tp_pct}}"
    conditions:
      all:
        - metric: zscore_basis
          op: ">"
          value: "{{z_entry}}"
        - metric: basis
          op: "<"
          value: 0
        - metric: close
          op: crosses_below
          value: sma_basis
          lookback: 2

exit_rules:
  - priority: 10
    action: CLOSE_LONG
    conditions:
      any:
        - metric: zscore_basis
          op: between
          value: "[-{{z_exit}}, {{z_exit}}]"
        - metric: pnl_pct
          op: "<="
          value: "{{sl_pct}}"
          source: position
        - metric: pnl_pct
          op: ">="
          value: "{{tp_pct}}"
          source: position
        - metric: bars_held
          op: ">="
          value: 360
          source: position
          description: "Вышел через 15 дней (360×60m), не дождавшись возврата"

  - priority: 20
    action: CLOSE_SHORT
    conditions:
      any:
        - metric: zscore_basis
          op: between
          value: "[-{{z_exit}}, {{z_exit}}]"
        - metric: pnl_pct
          op: "<="
          value: "{{sl_pct}}"
          source: position
        - metric: pnl_pct
          op: ">="
          value: "{{tp_pct}}"
          source: position
        - metric: bars_held
          op: ">="
          value: 360
          source: position

sweep:
  enabled: true
  params:
    z_entry: [1.5, 2.0, 2.5, 3.0]
    lookback_days:
      interval: [10, 40]
    sl_pct:
      range: [0.02, 0.05, 0.01]
    tp_pct:
      range: [0.05, 0.20, 0.05]
  max_combinations: 64
```

## Имплементация (план)

### Python модули

```
tqa_framework/
└── strategy_engine/
    ├── __init__.py
    ├── loader.py           # load + validate YAML
    ├── metrics.py          # compute metrics on bars
    ├── conditions.py       # evaluate conditions (AND/OR groups)
    ├── actions.py          # action dispatcher
    ├── templates.py        # {{param}} substitution
    └── sweep.py            # grid combinatorics
```

### Поток

1. `loader.load(path, runtime_params={symbol, risk_pct, tf})`
   - Парсит YAML
   - Валидирует (типы, ссылки, циклы)
   - Возвращает `StrategyConfig` dataclass

2. `evaluate_entry(bars, positions, config) → list[Signal]`
   - Вычисляет метрики (metrics.py)
   - Проходит entry_rules по priority, first-match
   - Возвращает Signal или None

3. `evaluate_exit(position, bar, config) → str`
   - Вычисляет position-метрики (pnl_pct, bars_held)
   - Проходит exit_rules по priority, first-match
   - Возвращает "hold" / "sl" / "tp" / "timeout"

### Интеграция с существующей архитектурой

| Компонент | Стратегия + detect.py | Strategy Engine |
|:----------|:---------------------|:----------------|
| Entry | `detect.py` — Python | `entry_rules` — YAML |
| Exit | `tick.py` — Python | `exit_rules` — YAML |
| Параметры | config dict | typed params |
| Grid | внешний grid/runner.py | in-file `sweep:` |
| Валидация | no-op | load-time |

**Сосуществование:** Оба подхода живут параллельно.
Стратегия может быть pure-YAML, pure-Python, или гибрид (YAML-правила + Python-коллбэк).

## Предложения по развитию

1. **Multi-ticker метрики** — ссылка на `{{symbol}}_spot` подразумевает multi-ticker режим. Добавить `data_sources:` секцию для явного указания второго тикера.

2. **Volume-based метрики** — CVD, DOM кластеры, OI дельты. Пока не в spec (требуют не-bar данных), но структура `source` (bar/position/market) расширяема.

3. **Индикаторы из TA-Lib** — SMA/zscore это built-in, но для MACD, RSI, Bollinger нужен plugin-механизм `indicator: talib.MACD(...)`.

4. **Time-based правила** — `session == "USA"`, `day_of_week in [1..5]`, `time_between(9:30, 16:00)`. Добавить в `conditions` через `source: time`.

5. **Long-stop** — guard: если стратегия не дала сигнал за N баров → force close. Пока не в spec.

6. **Combo-стратегии** — ссылка на другую `.strategy.yaml` как sub-стратегию. Например: MA crossover + basis filter. Пока не в spec.

7. **Производительность** — вычисление метрик на каждом баре может быть overhead для 50+ метрик. Ввести кэширование (lazy eval, только нужные метрики для данного бара).

8. **Параметры `{{symbol}}_spot`** — для basis-стратегий нужно два тикера (futures + spot/ETF). Уточнить синтаксис: `spot: SPY` в runtime config.

9. **Distance-based стопы** — не только % SL, но и ATR-мультипликаторы: `sl: "2.5 * atr_14"`. Добавить выражения в params для SL/TP.

10. **Визуализация** — провалидированная стратегия должна уметь генерировать SVG-диаграмму логики (как Excavator Draw в терминале).