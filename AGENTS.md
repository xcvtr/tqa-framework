# tqa-framework — Trading Framework

**Единый trading framework на все рынки: crypto, forex, MOEX.**
<!-- hindsight_tag: tqa-framework -->

## Архитектура

```
tqa-framework/
├── engine/                    ← ядро (общее для всех стратегий)
│   ├── __init__.py
│   ├── cli.py                 ← единый CLI entry point
│   ├── exchange_base.py       ← ABC: Position, Signal, ExchangeBase
│   ├── exchange_mock.py       ← Mock биржа (1m из CH/PG)
│   ├── exchange_binance.py    ← Live Binance API
│   ├── exchange_alor.py       ← Live MOEX (Alor API)
│   ├── exchange_mt5_bridge.py ← Live MT5 (forex/CFD)
│   ├── pg_state.py            ← PG: connect, CRUD, ensure_tables
│   ├── risk.py                ← common pool, sizing, trend filter
│   ├── detect.py              ← общий detect (resample, dedup)
│   └── backtester.py          ← портфельный бэктестер
├── config/
│   ├── schema.yaml            ← схема конфига
│   └── defaults.yaml          ← дефолты
├── grid/
│   └── runner.py              ← sweep параметров
├── strategies/                ← конкретные стратегии
│   ├── dragon/
│   │   ├── detect.py
│   │   ├── tick.py
│   │   └── configs/
│   ├── impulse_return/
│   ├── stop_hunt/
│   └── funding_rate_trap/
├── scripts/                   ← утилиты (монитор, миграции, отладка)
├── AGENTS.md                  ← этот файл
├── RULES.md                   ← правила
└── pyproject.toml
```

### Live vs Backtest

```
       ┌──────────────┐
       │    CLI       │
       │  engine.cli  │
       └──────┬───────┘
              │
     ┌────────┴────────┐
     │                 │
  --mode live      --mode backtest
     │                 │
     ▼                 ▼
 ┌──────────┐   ┌──────────────┐
 │ detect() │   │   detect()   │
 │ tick()   │   │ evaluate_    │
 │ executor │   │ position()   │
 │ (live)   │   │ executor     │
 └──────────┘   │ (mock)      │
                └──────────────┘
```

**Логика едина:** live и backtest используют одни и те же `strategies/<name>/detect.py`.
Разница только в executor: Binance/Alor/MT5 (live) vs ExchangeMock (backtest).

### Детект (таймфрейм `--tf`)

1. Загрузить M1 из CH/PG за последние N часов
2. Ресемпл → заданный TF (3m/5m/10m/15m/30m/60m)
3. `strategies/<name>/detect.detect()` — поиск паттернов
4. Dedup (symbol + direction + price)
5. Trend filter (SMA 50: блокирует контр-тренд после profitable)
6. Запись в PG `pending_signals`

### Тик (каждую минуту)

1. Загрузить состояние из PG (balance, positions, config)
2. Получить цены (exchange API / CH)
3. Для каждой позиции: SL → trailing → timeout
4. Открыть новые из pending_signals
5. Сохранить состояние + MTM snapshot

## Исполнители (executor'ы)

| Executor | Рынок | Комментарий |
|:---------|:------|:------------|
| `exchange_mock.py` | Любой | CH/PG как источник 1m бар |
| `exchange_binance.py` | Binance crypto | Live API |
| `exchange_alor.py` | MOEX | Alor API |
| `exchange_mt5_bridge.py` | Forex/CFD | MT5 bridge |

## Источники данных

- **ClickHouse** (10.0.0.60:8123, `db=moex`) — MOEX M1 бары
- **ClickHouse** (10.0.0.60:8123, `db=forex`) — forex M1 бары
- **PostgreSQL** (10.0.0.60:5432, `db=moex`) — состояние, конфиги
- **PostgreSQL** (10.0.0.60:5432, `db=forex`) — forex состояние
- **Binance/Bybit API** — crypto данные

## Аудит

- Комиссия: round-trip 8₽ (MOEX)
- Trend filter: без look-ahead (`m1[:i]`)
- Detect: без текущего бара
- Score: у всех стратегий есть score для multi-strategy
