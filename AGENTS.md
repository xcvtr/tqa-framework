# TQA-M — MOEX futures trading framework (new architecture)

**Принцип:** Детектор читает ТОЛЬКО CH `mt5_roll` (immutable сырьё), PG = state journal только. Бар никогда не входит в PG-state. Детектор один и тот же на test/paper/live — меняется только исполнитель/коннектор.

## Архитектура (скилл trading-strategy-architecture)

```
┌──────────────────────────┐
│ Detector (свой на стратегию) │ ← CH mt5_roll only, без БД-импортов
│ strategies/trend_sma/detect.py│   detect(bars, config) -> [Signal]
├──────────────────────────┤
│ Position-manager (решения)   │ ← SL / трейлинг (arm/пик/откат) / пирамидинг /
│ strategies/trend_sma/        │   scale-in / timeout / TP / сайзинг / ГО-гейты
│   position_manager.py        │   ничего не исполняет
├──────────────────────────┤
│ Executor (исполнение)        │ ← fill, slippage, ликвидность, комиссия, PnL
│ strategies/trend_sma/broker.py│  BrokerSim._close_market + check_liquidity
├──────────────────────────┤
│ Data / Connector             │ ← CH raw (data_provider) + PG state
│ tqa_f/data, tqa_f/state,     │
│ tqa_f/connector              │
└──────────────────────────┘
```

Контейнеры (`docker/compose.yml`): **loader** (`Dockerfile.loader`, единственный писатель в CH `mt5_roll` — roll ← continuous, только вперёд), **detector** (`Dockerfile.detector`, только detect+data+state+connector.exchange_base) и **position** (`Dockerfile.position`, всё остальное). Детектор в образе детектора не может импортировать executor — это и есть проверка слоя.

## Данные
- **CH:** ClickHouse 10.0.0.60:8123, db=moex, table=moex.mt5_roll (M1 continuous, canonical backtest data)
- **PG:** PostgreSQL 10.0.0.60:5432, db=moex, schema=futures (multi_state, multi_closed_trades, portfolio config), без пароля

## Запуск

Бэктест-паритет (28 тикеров, OOS 2026-01-01→11-11):
```bash
cd /home/user/projects/tqa-f && bash scripts/parity.sh     # PASS/FAIL
```
Один entrypoint на исполнитель (режим — флагом):
```bash
python scripts/position_cli.py --mode test --persist none --tickers MN
python scripts/detector_cli.py check                       # self-sufficiency
python scripts/loader_cli.py --loop 60                    # roll ← continuous (контейнер tqa-f-loader)
python scripts/detector_cli.py scan --tickers MN --dry-run  # CH → detect
python scripts/detector_cli.py scan --tickers MN --from 2026-10-20 --to 2026-11-13 \
  --schema tqa_f_sig                                      # CH → PG pending_signals
python scripts/position_cli.py --mode test --tickers MN --signals pg \
  --signals-schema tqa_f_sig --connector mock             # исполнитель читает сигналы из PG
```
Тесты: `PYTHONPATH=. python -m pytest tests/ -q`

## Спека AlfaForex (forex, 2026-11-19)
Источник: `archive/TQA/docs/market/alfaforex_specifications.md`. В `tqa_f/data/forex_specs.py`
теперь: спред в **пунктах** (круг, половина на вход/выход — применяется в engine+broker),
комиссия 0, своп long/short в пунктах за ночь (начисляется при закрытии по числу ночей),
маржа 2.5% → плечо 40 на всём форексе (док даёт золоту 6%, но на счёте 1:40) — из неё считается лимит залога.
Золото: контракт 100 унций, пункт 0.1, $10/пункт. 0.01 лота = 1 унция, маржа 264 USD при цене 4400.
Эталон после пересчёта: 182 сделки, +1621.37 USD, DD 25.4% (475 USD, 2019-2026).

## Эталон паритета (канон `backtest.summary` id=106)
`1382` сделки, ROI `+1033.91%`, MTM-DD `12.15%`, WR `41.03%`, equity `2 267 828`
(18.11 введены честные издержки: входной слиппедж 1 тик + комиссия за обе ноги;
 было 1381 / +1055.79% / 11.88% / 41.85% — недооценка издержек ≈2 п.п. ROI)
(канон: 1368 / +1042.9% / 12.10% / 41.74%). Любая правка слоёв обязана этот гейт проходить.

## Состояние
- ✅ detect.py — порт из TQA-MOEX-futures-framework, БД не трогает
- ✅ position_manager.py — решения вынесены из broker (перенос 1:1, паритет сохранён)
- ✅ broker.py — только исполнение (148 строк, решений нет)
- ✅ engine.py — ЕДИНСТВЕННАЯ реализация решений (run_days): по одному дню, M1-клок
- ✅ self_tester.py — драйвер истории (грузит период → run_days на все дни)
- ✅ live.py — драйвер live/paper: rolling-окно, реплей дня из day-start snapshot, журнал в PG (`LiveDB`), восстановление state при старте
- ✅ tqa_f/data/bars.py — слой данных (msk_date/group_days/resample5), доступен образу детектора
- ✅ detector_cli scan — сигналы на том же входе, что движок (149 дневных + формирующийся 5м), запись в PG или --dry-run
- ✅ docker/ — 2 образа + compose, scripts/ — CLI, tests/ — pytest
- ✅ runner.py — тонкая обёртка над self_tester (второй реализации больше нет)
- ✅ PG state-journal: `PaperDB` (multi_state + multi_closed_trades) в `tqa_f/state/pg_state.py`
- ✅ `--persist pg --pg-schema … [--reset-state]` пишет сделки и state из движка (идемпотентно)
- ✅ `--mode paper` в position_cli запускает live-петлю (было: молча гонял бэктест)
- ✅ live↔bt проверено: replay 2026-10-20→11-13 (MN,SG) = 6 сделок / −1 738.91 ₽ бит-в-бит, PG-журнал совпал
- ✅ connector-слой подключён: `ExchangeMock` (`tqa_f/connector/exchange_mock.py`), филл-цена идёт через `BrokerSim(connector=…)`, поведение без коннектора не изменилось
- ✅ разделение детектор→PG→исполнитель: `detector_cli scan --from/--to` пишет pending_signals, движок читает их через `PGSignalSource` (`--signals pg`); в этом режиме position-код НЕ импортирует detect
- ✅ рестарт папера не переигрывает день: `multi_state.last_ts` + `run_days(min_ts=…)`
- ✅ издержки честные: входной слиппедж + комиссия за две ноги; журнал в МСК (`tz=MSK`)
- ⏳ `--connector mt5` = NotImplemented (нет реализации MT5-коннектора)
- ✅ **IR (impulse_retrace)** — новая стратегия: волна импульса + scale-in на откате + пирамидинг + трейлинг (портировано из tqa-c). Доступно через `detector_cli scan --strategy ir`

## IR Strategy State (2026-11-19)
- ✅ Trailing SL code integrated (peak tracking, pyramid limiting on trail)
- ✅ Ticker set optimized: audjpy replaces audusd (+96.90% vs +3.69% ROI)
- ✅ i-parameter sweep complete: i=48 optimal (SUM ROI +292.42%, AVG MDD 4.61%)
- ✅ 7-ticker portfolio: all MDD < 15%, SUM ROI +292.42%
- ✅ Default params: i=48, trail=1.5%, scale_in=True, pyr_max=2, scale_levels=2
