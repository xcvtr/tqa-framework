# СПЕКА: Перенос live-контура FX-7 (TQA-FX-TOP) в tqa-framework на единый YAML-движок

Дата: 2026-09-11. Режим: только чтение обоих репо; прод не менять; TZ — всё UTC.

## 0. Резюме (для ревью)

FX-7 — это **Python-детектор** (M20 volume-profile кластерный lifecycle), а не простая
YAML-формула. Поэтому в tqa-framework он переносится как **Python-стратегия проекта** с
конфигом в YAML (философия framework: «стратегии живут в проектах, framework — движок»).
Единый код live=backtest достигается тем, что **детект + 9 гейтов живут в одном модуле**,
который вызывают ОБА контура (как сейчас `engine/live_gates.py`), а различие контейнеров
сводится к executor'у (live → `ExchangeMT5`, backtest → `ExchangeMock`) и схемам PG
(live → `fx_top.multi_state`, dry-run → `strategies_replay.*`).

Правило разделения:
- **Движок** (tqa_framework, изменяемое): `engine/gates.py` (обобщение live_gates),
  `engine/exchange_mt5_bridge.py` (реализация TODO-заглушки), CLI-роутинг, PGState.
- **Проект FX-7** (новый, в проекте): `strategies/fx7/detect.py` (обёртка lifecycle),
  `strategies/fx7/live.py` (live-контур), `strategies/fx7/config.yaml` (гейты+портфель).
- **IMMUTABLE** (не менять): `exchange_base.py`, `exchange_mock.py`, `exchange_binance.py`,
  `exchange_alor.py`, `backtester.py`, `detect.py`, `pg_state.py` — кроме `exchange_mt5_bridge.py`
  (это TODO-заглушка, исключение из иммутабельности).

---

## A. Текущая структура live-контура FX-7 в TQA-FX-TOP

### A.1 Поток данных (прод-цепочка)

```
paper_trader.open_signals()                     # engine/paper_trader.py:732  (live open, каждые 20 мин)
   └─ engine/live_engine.generate_candidates()  # engine/live_engine.py:33    (единая генерация)
        └─ engine/lifecycle.run(sym, now-3d, now)# engine/lifecycle.py:445    (детект на символ)
             ├─ engine/detector.detect()         # M20 volume-profile кластеры
             ├─ engine/cluster_manager.update()  # JSON state/clusters.json   (состояние кластеров!)
             └─ возвращает trades[]: {sym,direction,cluster_type,cluster_level,entry_price,
                                       exit_price,entry_time,exit_time,pnl_pips,hold_days,
                                       cluster_vol,swap_rate,spread_pts,pip_mult,adx_value}
   └─ 9 гейтов (ИНЛАЙН в open_signatures, строки 809-952)
   └─ write_mt5_signals()                        # paper_trader.py:186  → INSERT fx_top.mt5_signals (ENTER)
        └─ engine/mt5_bridge.execute_signal()    # mt5_bridge.py:310 — реальный MT5-исполнитель
             ├─ читает fx_top.mt5_signals WHERE NOT processed (load_signals:254)
             ├─ открывает/закрывает на MT5 (AlfaForexRU-Real, *rfd-суффикс)
             ├─ пишет fx_top.mt5_account, mt5_trade_log, mt5_loss_tracker
             └─ обратная синхронизация: paper_trader.sync_mt5() → state['positions']
```

### A.2 Как paper_trader.open генерирует и дросселирует сигналы (9 фильтров)

`generate_candidates` для каждого символа из FX-7, если `is_in_session(sym, now)`, вызывает
`lifecycle.run` со скользящим окном `(now-3d, now)`, `threshold=1.5`, `atr_break=True`,
`atr_k=0.5`, `atr_period=72` → сырые кандидаты. Затем `open_signals()` дросселирует их:

| # | Гейт (инлайн в paper_trader.open) | Порог/логика | Аналог в live_gates.py |
|---|---|---|---|
| 1 | existing_syms / dedup кластера | не входить, если пара уже в позиции; dedup по (sym, cluster_level, direction) | — (в open) |
| 2 | **CL3** consecutive-loss | пауза после 3 убыточных подряд по паре (`_pair_cl[sym]>=3`) | `gate_consecutive_loss` |
| 3 | **NET** | CH `forex.dom_clean`: вход только если толпа СБРАСЫВАЕТ (net_now < net_prev*1.2), fail-closed при ошибке CH | `gate_net` |
| 4 | **session** | `is_in_session(sym, now)` (SYM_CONFIG session) | `gate_session`/`is_in_session` |
| 5 | **correlation** | не 2+ позиции одного направления в валютной группе (JPY/USD/EUR/AUD/GBP/XAU) | `gate_correlation` |
| 6 | **ENTER-24h dedup** | не чаще 1 ENTER на пару за 24ч (запрос fx_top.mt5_signals) | `gate_enter_dedup` |
| 7 | **stale ±0.3%** | `abs(price-entry)/entry*100 <= 0.3` | `gate_stale_price` |
| 8 | **SMA72 sell-trend** | SELL запрещён при price>SMA72 (если `sell_trend_filter='sma72'`) | `gate_sma72_sell` |
| 9 | **mom3d** | вход только при падении/росте <0.5% за 3 дня | `gate_mom3d` |
| + | dircfg (чекп.090) | per-symbol направление (audjpy BUY, euraud SELL, eurgbp BUY, gbpjpy BUY, usdchf SELL) | `gate_dir_cfg` |
| + | last-profit-opposite | не входить против последней прибыльной | `gate_last_profit_opposite` |
| + | cluster_alive | `check_cluster_alive()` — кластер ещё жив | `gate_cluster_alive` |

> ВАЖНО (чекп.115, «Осталось»): в live `paper_trader.open` гейты сейчас **задублированы
> инлайн**, а `engine/live_gates.py` (единый модуль) пока вызывает ТОЛЬКО `tester.calc(live_gates=True)`.
> То есть «один код live=тестер» сегодня выполнен на стороне тестера, но live ещё не переключён
> на вызов `live_gates`. Перенос в framework — это шанс закрыть это сразу: оба контура вызывают
> один модуль гейтов.

### A.3 Генерация сигналов (lifecycle.run / generate_candidates) → что переносится в framework detect()

- **lifecycle.run(sym, start, end, ...)** — ядро детекта: M20-матрица объёма из
  `forex.dom_clean` + `forex.bars` → rolling detection → entry/exit по кластерам →
  возвращает готовые сделки с pnl_pips (учёт свопа/спреда). Это **детектор**; в framework он
  становится `strategies/fx7/detect.py::detect(bars, config) -> list[Signal]`.
- **live_engine.generate_candidates / generate_candidates_rolling** — оркестратор:
  на каждый символ в сессии вызывает lifecycle с окном, собирает кандидатов; rolling-версия
  повторяет 20-мин цикл для бэктеста. Это **единый входной контракт** — переносится как
  проект-функция `fx7_detect_all(symbols, now, gates_ctx)`.
- **cluster_manager** — состояние кластеров в **JSON файле** `engine/state/clusters.json`
  (не в PG). Для честного 1:1 live/backtest его надо перенести в **PG state** (см. риск R3).

### A.4 Точки интеграции с MT5 bridge

- `paper_trader.write_mt5_signals()` → `INSERT fx_top.mt5_signals(id,type='ENTER',action,symbol,
  entry,sl_pips,tp_pips,lot_size)`. ENTER-24h dedup читает ту же таблицу (`WHERE type='ENTER'
  AND created_at > NOW()-24h`).
- `mt5_bridge.py` читает эту таблицу (`WHERE NOT processed AND created_at > NOW()-30min`),
  исполняет `execute_signal` (stale >60мин скип, PYR без базовой позиции скип, EXIT по символу),
  пишет статус в `fx_top.mt5_account` (positions/equity/balance), `mt5_trade_log`, `mt5_loss_tracker`.
- Символьный маппинг `MT5_SYMBOL_MAP` (EURUSD → EURUSDrfd, AlfaForexRU-Real).
- `sync_mt5()` в paper_trader подтягивает фактическую позицию MT5 обратно в PG state
  (с сохранением pyramid_added/entry_time).

---

## B. Что уже есть в framework для live (переиспользуем / не переиспользуем)

| Компонент framework | Файл | Переиспользуем? | Комментарий |
|---|---|---|---|
| Live-контур LSR-CROSS (crypto) | `engine/paper.py` (1056 строк) | **Частично (паттерн, не код)** | `run_detect`/`run_tick`/`run_replay`/`scan_position` заточены под crypto (`long_short_ratio`, `klines_1m`, стратегия `lsr_cross`). Берём **паттерн**: PG multi_state (id=1), dry-run → `strategies_replay.*`, `run_replay` time-travel. Детект/тик FX-7 — свои. |
| PG multi_state / multi_closed_trades | `paper.py:71-102` (DDL) | **Да (схема)** | Генерализуем: schema=`fx_top` для live, `strategies_replay` для dry-run. |
| CLI paper-режим | `engine/cli.py:297 cmd_paper` | **Да (роутинг)** | Добавить `--strategy fx7`, executor `mt5`; dry-run/replay уже есть. |
| Executor ABC | `exchange_base.py` | Да | `Position`/`Signal`/`ExchangeConfig`/`ExchangeBase`. |
| MT5 executor-заглушка | `exchange_mt5_bridge.py` | **Да (точка реализации)** | TODO-заглушка `NotImplementedError` → реализовать live-форекс bridge по образцу TQA-FX-TOP `mt5_bridge.py`. Исключение из IMMUTABLE. |
| Mock executor (backtest) | `exchange_mock.py` | Да | Контейнер backtest (1m из CH/PG). |
| YAML-движок | `strategy_engine/` (parser, runtime, actions, metrics) | **Частично** | Полезен для конфига, НО FX-7 детект — Python, а не YAML-условие. `runtime.evaluate` не выражает «гейтовое дросселирование». Гейты — отдельный модуль. |
| Пример forex YAML | `strategies/eurgbp_gbpusd/config.yaml` | Да (шаблон) | Показывает формат `metrics`/`signals`/`exclude`/`risk`/`data_sources`, `cross_execute` action. |
| PGState | `engine/pg_state.py` | Да | `ensure_tables_live(schema)` (pending/positions/state) + CRUD. multi_state DDL сейчас в paper.py. |
| Risk/sizing | `engine/risk.py` | Да | `calc_lot_forex`, `calc_contracts`, common-pool концепт. |
| Backtester | `engine/backtester.py` | Частично | Контейнер backtest; FX-7 входы через единый gates. |

**Не переиспользуем напрямую:** CH-загрузчики/детект/тик `paper.py` (crypto), `detect.py`
(resample для MOEX-фьючерсов — не про M20 volume-profile), `backtester.py` (портфельный
универсальный — для FX-7 используем свой rolling backtest с gates, по образцу
`live_engine.generate_candidates_rolling`).

---

## C. Схема YAML для FX-7

### C.1 Текущие пробелы schema.yaml (config/schema.yaml)

Сейчас schema.yaml описывает только `strategy`, `tickers`, `risk(max_conc, base_risk)`,
`executor`. НЕ хватает (для 9 гейтов + портфеля FX-7):

1. **gates** блок — все 9 фильтров + константы (enter_dedup_hours, stale_price_pct,
   cl3_n, mom3d_threshold, correlation_groups, dircfg).
2. **per-symbol конфиг** (SYM_CONFIG): session, zr, net_share, min_vol, hold_days,
   sell_trend_filter, sl_pct, tp_pct, min_atr.
3. **detect** блок: detector_mode (volume), window_days, dom_resolution (M20),
   dom_bar_minutes, threshold, atr_break/atr_k/atr_period, max_entry_levels.
4. **portfolio/risk**: common-pool флаг (риск делится на слоты), max_conc (8),
   per-sym риск-веса, direction diversity.

### C.2 Предлагаемая схема `strategies/fx7/config.yaml`

```yaml
strategy: fx7
version: "1.0"

# Детектор — Python-стратегия проекта (обёртка над M20 lifecycle)
detect:
  module: fx7.detect              # strategies/fx7/detect.py
  detector_mode: volume
  window_days: 3
  dom_resolution: M20
  dom_bar_minutes: 20
  threshold: 1.5
  atr_break_enabled: true
  atr_k: 0.5
  atr_period: 72
  max_entry_levels: 1

symbols:
  - sym: euraud
    min_vol: 5
    net_share: 0.10
    hold_days: 20
    zr: 0.005
    session: [0, 24]              # UTC
    sell_trend_filter: sma72
    sl_pct: 1.0
    tp_pct: 2.0
    direction: SELL               # dircfg (чекп.090); пусто = both
    risk_w: 1.0
  # ... eurgbp(BUY), eurusd, audusd, usdcad, gbpusd, usdchf(SELL)

# 9 гейтов (единый код live=backtest)
gates:
  session: true
  enter_dedup_hours: 24
  stale_price_pct: 0.3
  cl3_n: 3
  net: true                        # fail-closed при ошибке CH
  sma72_sell_trend: true
  mom3d_threshold: 0.5
  cluster_alive: true
  last_profit_opposite: true
  correlation_groups:
    jpy: [audjpy, eurjpy, gbpjpy, usdjpy]
    usd: [audusd, eurusd, gbpusd, usdcad, usdchf]
    eur: [euraud, eurgbp, eurjpy, eurusd]
    aud: [audjpy, audusd, euraud]
    gbp: [gbpjpy, gbpusd, eurgbp]
    xau: [xauusd]

# Портфель / риск
risk:
  max_conc: 8                      # ЦЕЛЕВОЕ (в прод-коде сейчас MAX_CONC=6)
  base_risk: 1.0
  common_pool: true                # риск делится поровну на слоты (equity/max_conc)
  dd_stop_pct: 0.20
  max_margin_ratio: 0.8
  sl_pct: 1.5
  comission: 0.0001
  slippage: 0.0

executor:
  name: mt5
  testnet: false                   # AlfaForexRU-Real (rfd)
  mt5_symbol_suffix: rfd
```

> Примечание: `max_conc` в задании = 8, в текущем прод-коде paper open = 6 (чекп.059-062
> исторически двигали 5→6→…). В спеке фиксируем 8 как целевое значение в YAML, управляемое
> конфигом, а не хардкодом.

### C.3 Что расширить в framework (минимально)

- `config/schema.yaml` — добавить разделы `detect`, `gates`, расширить `risk`
  (common_pool, per-sym risk_w), `symbols[]` с per-sym полями.
- `engine/gates.py` (НОВЫЙ, движок) — обобщение `live_gates.py`: чистые предикаты,
  параметризованные конфигом (строится из YAML). Не хардкодит FX-7-константы.
- CLI `cmd_paper` — роутинг `--strategy fx7` на fx7-live контур.
- `exchange_mt5_bridge.py` — реализация executor (см. D, этап 1).
- Класс-контейнер `fx7` живёт в проекте (не в движке) — детект и live-тик.

---

## D. Карта переноса кода (файл → файл, по этапам)

Легенда: **К**опия/перенос, **А**даптация, **П**ереписать с нуля, **R**euse (без изменений),
**NEW** — новый файл. Направление «→» = из TQA-FX-TOP → в tqa-framework / проект FX-7.

### Этап 1 — Executor (live-форекс bridge)

| Из TQA-FX-TOP | → | В framework | Тип |
|---|---|---|---|
| `engine/mt5_bridge.py` (execute_signal, close_position, save_account_status, load_signals, mark_processed, should_skip_symbol/update_loss_tracker, MT5_SYMBOL_MAP) | → | `tqa_framework/engine/exchange_mt5_bridge.py` — реализовать `ExchangeMT5` (get_price/get_positions/open_position/close_position/get_account_balance), обёртка над MT5 API + чтение/запись `fx_top.mt5_signals`/`mt5_account`/`mt5_trade_log`/`mt5_loss_tracker` | А (TODO-заглушка → реализация) |
| `engine/paper_trader.py` (write_mt5_signals, sync_mt5, MT5_SYM_REVERSE) | → | FX-7 проект `strategies/fx7/bridge.py` (helper): запись ENTER в mt5_signals, обратная синхронизация позиций MT5→PG state | А |
| — | NEW | тест: mock MT5 (dry-run), проверка ENTER→mt5_signals→mock execute | NEW |

Выход: CLI `paper --strategy fx7 --executor mt5` реально открывает/закрывает на MT5,
состояние в `fx_top.mt5_account`, лог в `mt5_trade_log`. IMMUTABLE-файлы не трогаем.

### Этап 2 — Детект + гейты (единый код live=backtest)

| Из TQA-FX-TOP | → | В framework / проект | Тип |
|---|---|---|---|
| `engine/live_gates.py` (12 предикатов) | → | `tqa_framework/engine/gates.py` — обобщённые чистые предикаты, параметризованы config (from YAML): gate_session, gate_enter_dedup, gate_net, gate_consecutive_loss, gate_correlation, gate_stale_price, gate_sma72_sell, gate_mom3d, gate_cluster_alive, gate_dir_cfg, gate_last_profit_opposite | А (вынести константы в config) |
| `engine/lifecycle.py` (M20 volume-profile детект), `engine/detector.py`, `engine/cluster_detector.py` | → | FX-7 проект `strategies/fx7/detect.py::detect(bars, config)` → list[Signal]; внутренне использует volume-profile + rolling-окно, возвращает кандидатов (sym, direction, cluster_level, entry_price, entry_time, cluster_type) | А (переписать под контракт framework Signal, без записи в БД — чистый детект) |
| `engine/live_engine.py` (generate_candidates, generate_candidates_rolling) | → | FX-7 проект `strategies/fx7/detect_all.py` (или в detect.py): `detect_all(symbols, now, gates_ctx)` + `detect_all_rolling(...)` для бэктеста | А |
| `engine/cluster_manager.py` (JSON state) | → | **PG multi_state** (поле `clusters` в jsonb) — перенести состояние кластеров в PG, чтобы rolling live/backtest делили одинаковое состояние | П (см. риск R3) |

Выход: единый `engine/gates.py` вызывают и live-контур, и rolling-backtest; детект в проекте.

### Этап 3 — Live-контур (адаптация paper.py)

| Из TQA-FX-TOP / framework | → | Проект FX-7 | Тип |
|---|---|---|---|
| `tqa_framework/engine/paper.py` (паттерн multi_state, dry-run strategies_replay, run_replay) | → | FX-7 проект `strategies/fx7/live.py`: run_detect (детект+gates→pending), run_tick (MTM/SL/TP/trail/cluster-exit; открытие через ExchangeMT5, закрытие через gates/bridge), open→write ENTER в mt5_signals, sync_mt5 | А/П |
| `paper_trader.open_signals` (портфель common-pool, слоты equity/max_conc) | → | `strategies/fx7/live.py::open` | А |
| `engine/tester.py::calc(live_gates=True)` | → | FX-7 проект `strategies/fx7/backtest.py`: rolling-backtest, те же gates, ExchangeMock + dry-run schema strategies_replay | А |
| CLI | → | `tqa_framework/engine/cli.py::cmd_paper` — роутинг `--strategy fx7`, `--executor mt5`/`mock`, `--dry-run` | А |

Выход: раздельные контейнеры — live → `ExchangeMT5` + `fx_top.multi_state`; backtest →
`ExchangeMock` + `strategies_replay.*`. Оба зовут `engine.gates` + `fx7.detect`.

### Этап 4 — Верификация (parity live=backtest)

| Действие | Проверка |
|---|---|
| Rolling-backtest 2024-07-01→2026-07-01 с гейтами | Воспроизвести чекп.115: GATED ~71 сделка, WR ~78.9%, ret ~+130%, MTM DD ~8.0% (vs RAW ~+1346%@11%). |
| Сверка входов live vs backtest на одном периоде | Единый код гейтов: число/моменты входов должны совпадать при одинаковом состоянии кластеров. |
| End-to-end dry-run | `paper --strategy fx7 --executor mock --dry-run --replay ...` → сделки в `strategies_replay.multi_closed_trades`, live-таблицы не тронуты. |
| MT5 smoke | `paper --strategy fx7 --executor mt5` → ENTER в fx_top.mt5_signals → bridge открывает позицию; sync_mt5 подтягивает её в state. |
| Регресс crypto LSR-CROSS | `paper --strategy lsr_cross ...` (существующий) не сломан — живой контур не трогаем. |

---

## E. Риски и открытые вопросы

- **R1 — TZ (всё UTC).** FX-7 прод работает в UTC; `detect.py` framework местами трактует
  naive-строки как МСК (см. `_parse_ts`). При переносе детекта/гейтов строго нормализовать
  все метки в UTC (session, mom3d, SMA72, NET-окна). Несоответствие — источник расхождения входов.
- **R2 — «один код» ещё не выполнен в live.** В TQA-FX-TOP `live_gates.py` вызывает только
  тестер; live `open_signals` дублирует гейты инлайн. Перенос обязан собрать оба контура на
  `engine/gates.py` сразу, иначе расхождение (чекп.115: RAW +1346%@11% vs GATED +130%@8%)
  воспроизведётся заново.
- **R3 — состояние кластеров между циклами.** Сейчас кластеры в JSON-файле
  `state/clusters.json` (не в PG). Live вызывает rolling каждые 20 мин, backtest — разово;
  честное 1:1 (по чекп.115 «done-диагноз») требует **persistent cluster state в PG**,
  общего для live и rolling-backtest. Иначе разная частота → разная плотность входов.
- **R4 — разная плотность входов live vs backtest.** Live 20-мин rolling vs разовый backtest —
  сравнение «10 сделок/4дня» vs «71/2года» напрямую невалидно. Нужен rolling-backtest
  (`generate_candidates_rolling`) + тот же gates + тот же PG cluster state.
- **R5 — CH-зависимые гейты (NET/mom3d/SMA72).** Требуют запросов к `forex.dom_clean`/`forex.bars`
  на момент `entry_time`. В live это now, в backtest — исторический entry_time. Небольшой
  сдвиг времени → возможные расхождения в блок/пропуск. Унифицировать окна запросов.
- **R6 — YAML-движок vs Python-детект.** `strategy_engine/runtime` заточен под «условие →
  action» по барам и не выражает гейтовое дросселирование. Для FX-7 YAML = конфиг, а детект+гейты
  = Python. Не пытаться выразить M20-volume-детект в виде YAML-условий.
- **R7 — max_conc 6→8.** Целевое 8, прод сейчас 6. Разница влияет на риск/доходность;
  зафиксировать в YAML и перепрогнать верификацию (этап 4) с 8.
- **R8 — IMMUTABLE-файлы.** `exchange_base.py`, `exchange_mock.py`, `exchange_binance.py`,
  `exchange_alor.py`, `backtester.py`, `detect.py`, `pg_state.py` не менять.
  `exchange_mt5_bridge.py` — TODO-заглушка, реализация разрешена. Прод crypto-контур
  (paper.py LSR-CROSS) не ломать: FX-7 — отдельный модуль/проект.
- **R9 — FT/своп и pip_mult.** FX-7 считает свопы (`swap_rate`), спред и pip_mult (jpy=100,
  xau=10, иначе 10000). В framework риски считают в USD-фракциях; перенести pips-логику
  в fx7-проект, не трогая общий risk.py.
- **R10 — сбой CH при NET (fail-closed).** Прод блокирует вход при недоступности CH (fail-closed).
  Сохранить это поведение в `engine.gates.gate_net`, иначе входы пойдут «против фильтра».

---

## F. Решение по ключевым спорным пунктам

1. **Где живут гейты?** В **движке** (`engine/gates.py`), а не в проекте, — чтобы и live,
   и backtest (в т.ч. будущие стратегии) импортировали ОДИН модуль; константы — из YAML.
2. **Где живёт детект?** В **проекте FX-7** (`strategies/fx7/detect.py`) — движок не знает про
   M20 volume-profile. Соответствует философии framework («стратегии в проектах»).
3. **ExchangeMT5 реализуется в `exchange_mt5_bridge.py`** (это TODO-заглушка, исключение из
   IMMUTABLE), по образцу `mt5_bridge.py`; если потребуется второй MT5-сценарий — новый класс
   в новом файле (правило «новый сценарий = новый класс»).
