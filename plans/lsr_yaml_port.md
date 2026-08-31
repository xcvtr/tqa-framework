# LSR-CROSS → YAML: архитектура портирования

## Текущее состояние (backtester.py уже умеет)

1. Загружает LSR z-score сигналы из CH для crypto (строки 379-387)
2. Ремапит LSR timestamps на следующий 5m бар (строки 389-411)
3. Строит sym_ext_idx → передаёт external_signals в YAML evaluate() (строки 446-458)
4. YAML метрика `external.zscore` читает сигнал из state (runtime.py:41-50)
5. Exclude фильтры из YAML применяются (строки 413-444)

## Что добавить (новый brick: 1m hi/lo execution)

### 1. Python action `lsr_execute`
Функция, которая регистрируется через `register_action()`:
- Получает: symbol, price (вход), **5m bars**, **1m bars**, config (SL/TP/trail params)
- Запускает `simulate_trade()` логику: entry по close следующего 5m бара, exit сканированием 1m hi/lo
- Возвращает: entry_px, exit_px, exit_ts, pnl_eff, mtm_dd_pct (в виде структуры Signal с доп. полями)

### 2. Pre-compute trades (как в LsrCrossBacktester.run())
Вместо evaluate() на каждом bar_time:
- При external signal → вызываем `lsr_execute` сразу
- Получаем pre-computed trade (entry_ts, exit_ts, pnl)
- Добавляем в список pre-computed trades
- Portfolio loop открывает по entry_ts, закрывает по exit_ts

### 3. YAML стратегия lsr_cross.strategy.yaml
```yaml
name: lsr_cross
version: "2.0"
metrics:
  zscore:
    type: external
    params: { field: zscore }
signals:
  - id: lsr_long
    priority: 1
    when: "zscore > 2.0"
    then: lsr_execute
    params:
      direction: LONG
      sl_pct: 0.05
      tp_pct: 0.18
      trail_act: 0.04
      trail_dist: 0.03
      trail_lock: 0.02
      hold_h: 120
  - id: lsr_short
    priority: 1
    when: "zscore < -2.0"
    then: lsr_execute
    params:
      direction: SHORT
      sl_pct: 0.05
      tp_pct: 0.18
      trail_act: 0.04
      trail_dist: 0.03
      trail_lock: 0.02
      hold_h: 120
risk:
  pyramiding:
    trigger: 0.05
    add: 0.5
    max: 1
```

### 4. Файлы для изменения

1. `tqa_framework/strategy_engine/actions.py` — добавить `lsr_execute`
2. `tqa_framework/strategy_engine/metrics.py` — добавить `external` metric type (если нет)
3. `tqa_framework/engine/backtester.py` — в `run()`: для стратегий с external_signals → pre-compute trades через `lsr_execute`, портфельный цикл по exit_ts
4. `config/schema.yaml` — обновить если нужно
5. Создать `strategies/lsr_cross/config.yaml` — YAML конфиг

### 5. Верификация

После портирования: прогнать CLI --strategy-yaml lsr_cross и сравнить 1:1 с Python LsrCrossBacktester:
```
python3 -m tqa_framework.engine.cli --ch-db crypto backtest --tickers BTCUSDT,... --strategy-yaml lsr_cross --tf 60 --days 1095 --risk-pct 0.14 --equity 1000 --max-conc 6
```

Ожидаемый результат: ret=+916.51% DD=18.92% trades=1319 Calmar=48.44