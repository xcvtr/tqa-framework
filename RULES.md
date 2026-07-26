# RULES.md — tqa-framework

## 1. Фреймворк — не монолит

**Запрещено создавать ad-hoc скрипты.** Вся логика в `strategies/<name>/`.

Единый entry point:
```bash
# CLI — единственный способ запуска
python -m engine.cli --help

# Бэктест
python -m engine.cli backtest \
    --tickers GD,GZ,RN \
    --days 365 \
    --risk-pct 2 \
    --tf 60 \
    --strategy dragon

# Grid sweep
python -m engine.cli grid \
    --strategy dragon \
    --params '{"tf": [3,5,10], "risk_pct": [1,2,3]}'

# Live paper trader
python -m engine.cli paper \
    --strategy dragon \
    --executor binance
```

## 2. Executor'ы иммутабельны

- **Не трогать прод.** `exchange_binance.py`, `exchange_alor.py`, `exchange_mt5_bridge.py` — только bugfix
- **Новый рынок = новый класс**, наследующий `ExchangeBase`
- **Проверка:** перед изменением executor'а — `git diff --stat` показать пользователю

## 3. Detect ≠ Tick

| Компонент | Таймфрейм | Что делает |
|:----------|:----------|:-----------|
| **Detect** | `--tf N` (3m/5m/10m/15m/30m/60m) | Поиск сигналов на ресемпле |
| **Tick** | M1 | SL/TP/trailing/timeout, открытие новых |
| **SL/TP** | M1 | Проверка каждый тик |

Эффект: сигналы качественнее, шум M1 отфильтрован.

## 4. Common pool

Капитал общий (не per-symbol). Risk % от всего капитала на сделку.
GO лимит: `ct = min(ct_risk, ct_go)`

## 5. Конфиги — YAML, не код

```yaml
# config/strategies/dragon.yaml
tickers:
  - symbol: MM
    tf: 3
    risk_pct: 2
  - symbol: GZ
    tf: 5
    risk_pct: 2
```

Параметры стратегии — в YAML, не в `detect.py`.

## 6. Новая стратегия = новая папка

```
strategies/<name>/
├── detect.py     # signal detection (использует engine/)
├── tick.py       # per-tick: exits, opens, MTM
├── configs/      # YAML конфиги
└── scripts/      # кастомные утилиты
```

## 7. PnL расчёт (MOEX)

```
PnL = (exit - entry) / min_step * step_price - commission  # БЕЗ *lot
```

`lot_volume` не участвует — он уже учтён в `step_price`.

## 8. Данные

- **ClickHouse** (10.0.0.60:8123) — бары (M1)
- **PostgreSQL** (10.0.0.60:5432) — состояние, конфиги, сделки
- **Binance/Bybit API** — crypto данные

## 9. SIGPIPE

```python
signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # не SIG_DFL
```

## 10. Линтер обязателен

После каждого изменения `.py`/`.json`/`.yaml`/`.toml` — проверка синтаксиса.
Дважды проверять перед отчётом: файл создан, скрипт работает, данные свежие.
