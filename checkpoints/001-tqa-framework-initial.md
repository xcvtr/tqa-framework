---
title: "tqa-framework initial — backtester, test PG, CLI, report"
checkpoint: 1
date: 2026-07-27
tags: [checkpoint, tqa-framework, framework]
---

# Checkpoint 001 — tqa-framework initial

**Единый trading framework на все рынки: crypto, forex, MOEX.**

## Что сделано

### Тестовый PostgreSQL
`docker/compose.yml` — PostgreSQL 16 на `localhost:5433`, изолирован от прода (`10.0.0.60:5432`). Схемы: `futures`, `backtest`, `shared`, `mt5`, `fx_top`. Утилита `docker/pg.sh {start|stop|reset|psql}`.

### engine/pg_state.py
Реализован PG коннект через `PG_URL` env (дефолт: `localhost:5433/tqa`):
- `backtest.trades` — сделки
- `backtest.equity_curve` — equity по барам
- `backtest.summary` — сводка (Return, MDD, WR, PF, Calmar)
- Live таблицы: `pending_signals`, `positions`, `state` (через `ensure_tables_live()`)

### engine/detect.py
Реализованы:
- `load_m1_from_ch()` — загрузка M1 баров из ClickHouse (таблица `moex.bars`, колонки `bt/opn/hi/lo/prc/vol`)
- `resample_bars()` — ресемпл M1 → N-минутные бары
- `dedup_signals()` — дедупликация по (symbol, direction, price)

### engine/backtester.py
Портфельный бэктестер:
1. Загрузка стратегии через `importlib` (два формата: `strategies.<name>` и `<name>`)
2. `importlib.import_module()` с `sys.path.insert(0, path)` для `--strategy-path`
3. Перебор баров → вызов `detect()` + `tick()` из стратегии
4. Common pool equity, risk management (calc_risk_mult, calc_contracts)
5. MTM расчёт на каждом баре
6. Запись результатов в PG

### engine/cli.py
Команды:
- `backtest` — запуск бэктеста
- `grid` — sweep параметров
- `paper` — заглушка для paper trader
- `results` — просмотр результатов из PG (список, детали, сделки)

### ~/scripts/bt_report.py
Генератор отчёта бэктеста:
- Equity curve (Plotly, тёмная тема) + MDD зона (нижний subplot)
- Таблица метрик: Return, MDD, WR, PF, Calmar, Sharpe≈, годовая
- PNG через kaleido (scale=2x, 1000×480)
- MEDIA: для отправки в текущий чат
- Сохраняет в `~/.hermes/browser_screenshots/` для доступа

### strategies/test_ma/
Тестовая стратегия для проверки пайплайна: MA crossover с SL/TP.

### Обновлён скилл ohlc-viz
Добавлена документация `bt_report.py`, исправлена отправка через MEDIA: в текущий чат.

## Архитектура

```
Один образ → разные команды через --strategy

pip install tqa-framework

python -m engine.cli backtest \
    --tickers MIX \
    --strategy dragon \
    --strategy-path ~/projects/TQA-MOEX \
    --tf 60 --days 365
```

Framework — библиотека. Стратегии в отдельных проектах, подключаются через `--strategy-path`.

## Тестовые метрики

Стратегия `test_ma` (MA crossover) на MIX:
- Период: 180 дней (янв-июн 2026)
- TF: 60m
- Бэктест: +0.15%, MDD 0.07%, WR 66.7%, 6 сделок, Calmar 2.09
- Параметры: fast_ma=5, slow_ma=20, sl_pct=2%, tp_pct=3%

## Файлы

- `engine/pg_state.py` — реализован
- `engine/detect.py` — реализован
- `engine/backtester.py` — реализован
- `engine/cli.py` — реализован
- `pyproject.toml` — зависимости
- `docker/compose.yml` — тестовый PG
- `docker/pg.sh` — утилита
- `docker/init/01-schemas.sql` — схемы
- `strategies/test_ma/detect.py` — тестовая
- `strategies/test_ma/tick.py` — тестовая
- `scripts/bt_report.py` — отчёт (скопирован из ~/scripts/)

## Что не сделано

- `cmd_paper()` — заглушка (TODO: реализовать paper trader loop)
- `exchange_binance.py`, `exchange_alor.py`, `exchange_mt5_bridge.py` — заглушки (NotImplementedError)
- `grid/runner.py` — заглушка
- CH данные: таблица `moex.bars`, но нет forex/crypto
- git репозиторий не инициализирован
