# LSR-CROSS: gap-анализ live-контура (TQA-crypto, Python) vs YAML-движок (tqa-framework)

**Дата аудита:** 2026-08-31
**Автор:** субагент Hermes (read-only аудит, ничего не редактировалось: ни код, ни PG, ни cron)
**Статус:** ❌ **1:1 пересадка live paper-трейдера на YAML-движок в текущем состоянии НЕБЕЗОПАСНА** (расхождения по sizing, exclude_syms, dd_stop, сигнальному контуру, MTM-семантике; см. раздел «Вердикт»)

---

## 0. Снимок live-состояния (ground truth, PG 10.0.0.60/crypto, 2026-08-31)

| Параметр | Значение |
|---|---|
| equity / peak / balance | **544.42 / 547.13 / 368.61** |
| Открытые LSR-позиции | **2** (не 16 — 16 позиций это legacy dragon-папер из AGENTS.md) |
| OPUSDT SHORT @0.0930, eq_open=475.0, risk=0.132 (=0.088×1.5, **пирамида**), trail_active=true, trail_peak=0.085, margin=62.7, ts_close=09-02 | открыта 08-28T03:10 |
| DOGEUSDT SHORT @0.08472, eq_open=546.18, risk=0.08, trail_active=false, margin=43.69, ts_close=09-02 | открыта 08-28T19:10 |
| Закрытые LSR-сделки | 3: APT +71.18 (trail), NEAR +0.95 (trail, с пирамидой ×1.5), ARB **−2.71** (sl) |
| strategy_config (JSONB, id=1) | th=2.0, z_window=720, base_risk=0.08, leverage=1, max_pos=6, sl_pct=0.05, tp_pct=0.18, **trail_act=0.05, trail_dist=0.06**, trail_lock=0.02 (default в tick), pyr_trigger=0.05, pyr_add=0.5, comm=0.001, slip=0.0005, dd_stop_pct=0.25, max_margin_ratio=0.8, timeout_bars=120, exclude_hours=[11,22,23], exclude_dow=[2], exclude_syms=[SOLUSDT], sym_risk (12 символов) |

**Таймлайн equity (сверка, воспроизводится по live-формуле):**
475 → APT +71.18 → 546.18 → NEAR +0.95 → 547.13 → ARB −2.71 → **544.42** ✓
- ARB (единственная полностью post-fix сделка): `547.13 × (0.08×1.2) × 1 × (−0.0515) = −2.71` ✓ — точное совпадение с записью. Подтверждает live-формулу `pnl_usd = eq_open × base_risk × sym_risk × lev × (pnl_pct − comm − slip)`.
- NEAR: `475 × (0.08×0.9) × 1.5 (пирамида) × 0.0185 = 0.949 ≈ 0.95` ✓ — подтверждает, что пирамида масштабирует risk ×1.5 относительно **eq_open** (equity на момент тика открытия, а не на момент входа сигнала).
- Оба trail-выхода (APT, NEAR) закрылись ровно на уровне trail_lock=+2% (exit = 0.98×entry) — живой трейл исполняет выход строго по уровню, как simulate_trade.

---

## 1. Механика входа и выхода (SL/TP/trail/pyramid/hold, окно сканирования 1m)

### Live (Python)
- **Вход:** detect.py пишет событие кросса z в `pending_signals` (ts = ts события, направление из кросса). tick.py при следующем тике открывает по `last_close` последнего 1m бара из окна 60 мин → **вход = ближайший 1m close после сигнала** (детект :05, тик каждую минуту → задержка ~1–6 мин).
- **Скан (tick.py `scan_position`, окно 60 мин 1m баров):** на каждом баре в порядке времени: пирамидинг (hi≥pyr_px / lo≤pyr_px) → trail (активация по hi/lo, SL=entry при активации, отступ trail_dist от пика, floor trail_lock) → **SL → TP**, выход **строго по уровню** (exit_px = sl_px/tp_px уровня). Состояние между тиками переносится в position dict (`trail_peak`, `trail_active`, `pyramid_added`).
- **Timeout:** ts_close = ts_open + timeout_bars(120 ч), выход по последнему close.
- **Пирамида:** risk ×1.5, margin/contracts ×1.5, применяется перед закрытием на том же тике.
- **PnL:** `pnl_usd = eq_open × risk(вкл. пирамиду) × lev × (pnl_pct − comm − slip)`.

### YAML-движок
- `_run_lsr_mode` (backtester.py:1025) — pre-compute: для каждого external-сигнала вызывается action `lsr_execute` (actions.py:127), который сканирует 1m hi/lo в том же порядке **pyr → trail → sl → tp**, выход по уровню, hold = hold_h×60 (120 ч), comm_slip хардкод 0.0015. Затем портфельный цикл на 1m барах.
- **Вход в action:** `si = searchsorted(1m, ev_ts, 'right'); entry = close 1m[si]` — но ev_ts перед этим **ремапится** (backtester.py:397-411) на ts ближайшего бара **tf-таймфрейма** (по умолчанию из CLI `--tf`). При `--tf 5`: entry ≈ 1m close через 1–6 мин после события — **совпадает с live**. При `--tf 60` (пример в plans/lsr_yaml_port.md!): entry = 1m close после следующего часового бара — сдвиг до 60 мин, **НЕ совпадает ни с live, ни с Python-бэктестом**.

### Расхождение Python-бэктест vs YAML (важно для сверки «1:1 с бэктестом»)
- `engine/lsr_cross.py simulate_trade` (строка 157): вход = close **следующего 5m бара** (searchsorted по 5m), скан начинается после закрытия 5m бара входа. YAML `_lsr_execute` входит по 1m close после открытия 5m бара → entry на ~4–5 мин раньше и по другой цене. На 1m hi/lo исполнении это меняет исход маргинальных сделок (SL/TP на границе).
- **Trail-константы:** Python-бэктест использует хардкод `TRAIL_ACT=0.02, TRAIL_DIST=0.015` (engine/lsr_cross.py:32-33); live tick.py берёт **PG override 0.05/0.06** (pg_state.load_lsr_config → cfg['trail_act']); YAML читает из сигнальных params 0.05/0.06. **Вывод: в live исполняется PG-конфиг trail_act=0.05/trail_dist=0.06** (подтверждено содержимым strategy_config и шапкой YAML). Явный внутренний разрыв в Python-мире: бэктест (0.02/0.015) ≠ live (0.05/0.06). YAML-движок в этом параметре совпадает с live, но НЕ с Python-бэктестом.
- Порядок проверок в баре совпадает (pyr→trail→sl→tp), выход по уровню совпадает, hold совпадает (120 ч).

**Вердикт по п.1: Частично совпадает.** Семантика выхода (1m hi/lo, порядок pyr→sl→tp, уровень-филл, trail_lock-флор, hold 120ч) — совпадает. Вход: YAML ≈ live (1m close после события) при `--tf 5`; Python-бэктест систематически отличается (~4–5 мин, 5m close); при `--tf≠5` YAML-вход ломается полностью. Trail-константы: YAML = live = PG (0.05/0.06), Python-бэктест = 0.02/0.015 (другое поведение).

---

## 2. MTM equity / DD

### Live
- tick.py (шаг 3): `mtm_dd_sum = Σ risk×lev×max(0, adverse_move_by_current_close)` (только неблагоприятные отклонения, по последнему close), `eq_mtm = equity − equity×mtm_dd_sum`; peak обновляется по eq_mtm; **DD-stop: при dd > dd_stop_pct×100 (25%) — принудительно закрываются ВСЕ позиции** (reason='dd_stop') и detect.py блокирует новые входы (is_dd_stopped).
- Python-бэктест (engine/lsr_cross.py:302-306): при открытии сделки сразу вычитается **худший за всю сделку** mtm_dd (worst по 1m), peak/DD считаются по этой консервативной кривой. DD-stop в бэктесте **отсутствует**.

### YAML (_run_lsr_mode)
- Портфельный цикл на 1m close: `port_value = cash + Σ eq_at_entry × _rr × signed_mtm_pnl` — MTM **знакопеременный** (учитывает и прибыль, и убыток), peak и max_dd считаются по нему. `mtm_dd_pct` из action **не используется** в equity-цикле.
- Расхождения: (а) знакопеременный vs adverse-only (у live DD-stop); (б) вклад открытой позиции без пирамидального множителя (pos_value без base_risk — для пирамидальных позиций занижение MtM в 1.5 раза); (в) **DD-stop 25% в YAML-движке отсутствует полностью** — торговля продолжается сквозь просадку; (г) баланс/маржа не ведутся (cash = equity без margin-блокировки).

**Вердикт по п.2: Не совпадает.** YAML-движок не реализует DD-stop live (25%), MTM-семантика другая (signed vs adverse-only), peak/DD-кривые несопоставимы с live-критерием остановки. Для пересадки обязательна реализация adverse-only MTM + принудительное закрытие при 25%.

---

## 3. Sizing / риск

### Live
- `risk = base_risk × sym_risk[sym]` (PG: 0.08 × {ADA 1.3, ARB 1.2, OP 1.1, AVAX 1.05, NEAR/APT 0.9, прочие 1.0}), `notional = equity × risk × lev(1)`, `margin = notional/lev`, лимит `Σ margin ≤ equity × max_margin_ratio(0.8)` (проверка и при открытии, и при пирамиде), max_pos=6 (с учётом pending в detect). Пирамида ×1.5 к risk/margin/contracts.
- Подтверждено на ARB: −2.71 = 547.13×0.096×1×(−0.0515) — sym_risk 1.2 **реально применяется** в live.

### YAML (_run_lsr_mode)
- `_rr(sym) = self.risk_pct × _raw.get('sym_risk').get(sym) × _lev` — но **`_raw.get('sym_risk')` = None**, т.к. в YAML sym_risk вложен в `risk:` (подтверждено парсингом YAML: top-level ключи = strategy/version/metrics/signals/exclude/risk/data_sources; top-level `sym_risk` и `params` отсутствуют) → `_sym_risk = {}` → **все sym_risk-множители молча отброшены** (риск UNISYM 1.0 для всех). Проверка: чекпойнт 009-lsr-cross-live-yaml-verify.md — ARB yaml дал бы −2.25 вместо −2.71 (занижение 17%).
- `_lev = raw.get('params',{}).get('leverage', 1.0)` — top-level `params` нет → 1.0. Совпадает с live (lev=1) **только случайно**; при изменении leverage в PG/YAML движок не последует (dead config).
- `risk_pct` берётся из **CLI `--risk-pct`** (по умолчанию **0.15**!), а не из YAML risk.base_risk (0.08). Если запустить без `--risk-pct 0.08` — риск незаметно 0.15.
- max_pos: используется `self.max_conc` (CLI `--max-conc`, default 6) — совпадает с YAML risk.max_pos=6 и live max_pos=6 только пока CLI не меняли.
- Комиссия/слиппедж: в action **хардкод** `comm_slip = 0.0010+0.0005` (actions.py:182); секции YAML `risk.comission/slippage` — мёртвый конфиг (совпадает с live только сейчас).
- Формула долларового PnL на закрытии: `eq_at_entry × _rr × pnl_eff` — pnl_eff уже содержит пирамидальный множитель (×1.5) — тут паритет с live есть, НО из-за п.1 (sym_risk) итоговый риск ×0.9..×1.3 неверен.

**Вердикт по п.3: Не совпадает.** Критический баг: `backtester.py:1068 — _raw.get('sym_risk')` читает верхний уровень YAML, а sym_risk лежит в `risk.sym_risk` → множители 0.9–1.3 не применяются (доказано на ARB: −2.25 vs −2.71). Плюс: CLI risk_pct (default 0.15) вместо base_risk 0.08; leverage из несуществующего top-level `params`; comm/slip хардкод; max_margin_ratio в YAML-пути не учитывается вообще.

---

## 4. Exclusion (hours={11,22,23} UTC, dow={2}, syms={SOLUSDT})

### Live
- detect.py (строки 48–76): фильтры применяются **по ts события** (час/день недели события кросса, UTC) и **до** записи в pending; exclude_syms отсекает SOLUSDT; дополнительно: не более 1 позиции на символ, пропуск символов, закрытых <7 дней назад, лимит с учётом pending.

### YAML
- В `_run_lsr_mode`-пути: exclude hours/dow применяются в run() (backtester.py:413-444) к `all_external_signals`, но **после ремапа ts на tf-бар** → фильтр по часу ремапленного ts, не события. На границах часов (событие 10:56–10:59 → ts 11:00-бар) событие ошибочно отсеется, а событие 11:56–11:59 — ошибочно пройдёт. При DOW-границе (23:58 → 00:00 след. дня) — аналогично.
- **`exclude.syms` (SOLUSDT) в YAML-пути НЕ применяется вообще** — в run() нет фильтра по syms (фильтруются только hours/dow), а `_run_lsr_mode` прекомпутит сделки по всем external-сигналам всех загруженных тикеров. Если SOLUSDT в CLI-списке тикеров — по нему будут входы (в live — никогда; символ исключён как «слабый, avg +0.81%»).
- В старом специализированном бэктестере LsrCrossBacktester (backtesters/lsr_cross.py:216-221, 252) exclude_syms применяется (классовое {SOLUSDT}, переопределяется из strategy_params), но exclude_hours/dow через CLI-путь **обнуляются** (params не содержат exclude_hours → set()), т.е. и он не воспроизводит live-фильтры без явных --params.

**Вердикт по п.4: Не совпадает.** YAML-путь пропускает SOLUSDT (критично: исключённый «слабый» символ будет торговаться) и фильтрует часы/дни по ремапленному ts с ошибкой на границах (≤5 мин при tf=5, до 60 мин при tf=60).

---

## 5. Триггер входа: событие vs состояние

### Live
- `detect_events` (engine/lsr_cross.py:98-117): чисто событийная семантика — `up = (z_prev < +2.0) & (z >= +2.0)` → SHORT; `dn = (z_prev > −2.0) & (z <= −2.0)` → LONG. Вход **только в момент пересечения**, не по состоянию z>2 (иначе был бы шквал повторов). Z_WINDOW=720 (rolling 30д почасовых), min_periods=100.

### YAML
- `when: "zscore > -999"` в YAML — декоративный и в `_run_lsr_mode` **вообще не оценивается**: движок вызывает action `lsr_execute` напрямую для каждого external-сигнала. Реальный триггер — наличие сигнала кросса от `load_lsr_data` (backtester.py:761-877), который воспроизводит ту же пару условий `zprev<th ≤ zcurr` / `zprev>−th ≥ zcurr` с порогом z_score_threshold=2.0 (параметр `z_score`). Семантика «событие, а не состояние» **сохранена**.
- Нюансы:
  - z-скор в движке: `sigma = sqrt(var)` — **популяционное** std (ddof=0); pandas в Python — **выборочное** std (ddof=1). Пограничные кроссы могут отличаться на волосок.
  - движок не имеет порога «≥10 событий на символ / ≥2000 LSR / ≥5000 klines» из Python (отсев тонких символов) — для 12 ликвидных символов несущественно.
  - В generic-пути (не LSR-precompute) `lsr_execute` молча возвращает [] (нет `external_signal` в state) — рабочим является только `_run_lsr_mode`.

**Вердикт по п.5: Совпадает** по сути (event-driven), с оговорками: ddof z-скора, отсутствие минимальных порогов данных, декоративность `when`.

---

## 6. PG-состояние

### Live пишет
- `strategies.multi_state` (id=1): equity, peak (через `GREATEST(peak, eq)`), balance, positions JSONB, pending_signals JSONB, strategy_config JSONB (источник истины — load_lsr_config).
- Position-схема: `{symbol, direction, strategy='lsr_cross', entry_price, risk, eq_open, contracts, margin, ts_open, ts_close, pyramid_added, z_score, leverage, trail_peak, trail_active}`.
- Закрытые: `strategies.multi_closed_trades` (strategy, symbol, direction, pnl_usd, pnl_pct, ts_open, ts_close, entry_price, exit_price, reason, bars_held).

### YAML-движок
- Бэктест пишет в `backtest.summary/trades/equity_curve` — другая схема, для live-реплея непригодна.
- **Live-режима в YAML-движке нет**: `cli.py cmd_paper` (строка 275) — заглушка «TODO: реализовать paper trader loop». Перенос live = **написание нового live-контура** поверх strategy_engine (detect→pending_signals→tick с executor'ом 1m из CH), а не «переключение флага».
- Для работы с тем же state live-контур YAML должен: читать strategy_config через тот же контракт (trail_act/trail_dist и пр. из PG, а не из констант!), сохранять те же поля позиций (eq_open, margin, trail_peak/trail_active между тиками), обновлять equity/peak/balance с margin-семантикой (balance -= margin при открытии/пирамиде, += margin при закрытии), писать multi_closed_trades, уважать max_margin_ratio и pending-лимиты.

**Вердикт по п.6: Не совпадает.** Нет live-контура в tqa-framework; бэктестовая схема PG другая; перенос требует реализации full live-loop с сохранением контракта multi_state.

---

## Таблица расхождений (файл, строка, поведение, риск)

| # | Файл:строка | Python live/backtest | YAML-движок | Риск для денег |
|---|---|---|---|---|
| 1 | backtester.py:1068-1073 vs tick.py:160 | `risk = base_risk × sym_risk[sym]` (ARB: ×1.2) | `_raw.get('sym_risk')` читает top-level → None → **sym_risk отброшен** (в YAML лежит в `risk.sym_risk`) | **КРИТИЧЕСКИЙ**: размер позиций 0.9–1.3× не тот; на ARB: −2.25 вместо −2.71 (−17%) |
| 2 | backtester.py:353-387,413-444 vs detect.py:71 | SOLUSDT исключён до детекции | `exclude.syms` нигде не применяется; SOLUSDT торгуется, если в CLI-тикерах | **КРИТИЧЕСКИЙ**: входы по исключённому «слабому» символу |
| 3 | tick.py:253-292 vs _run_lsr_mode:1152-1193 | DD-stop 25%: закрытие всех + блок входов | **DD-stop отсутствует**; торговля сквозь просадку | **ВЫСОКИЙ**: при 25%+ просадке live останавливается — YAML продолжит |
| 4 | cli.py:38 vs YAML risk.base_risk | live base_risk=0.08 | используется CLI `--risk-pct` (default **0.15**) | **ВЫСОКИЙ**: почти 2× риск при забытом флаге |
| 5 | backtester.py:397-411 vs engine/lsr_cross.py:171 | вход: close следующего 5m бара после события | ev_ts ремапится на tf-бар; entry = 1m close; при `--tf 60` сдвиг до 60 мин | **ВЫСОКИЙ (при tf≠5)** / средний (tf=5: ±4-5 мин от Python-бэктеста) |
| 6 | backtester.py:413-444 vs detect.py:73-75 | фильтр по часу/DOW ts события | фильтр по ремапленному ts (границы часов/дней сдвигаются ≤5 мин, при tf=60 — до 60 мин) | **СРЕДНИЙ**: пограничные сигналы (11:56–11:59, ср 23:58) уходят/приходят ошибочно |
| 7 | actions.py:182 vs tick.py:236 | comm/slip из PG cfg (0.001/0.0005) | хардкод 0.0015 в action; YAML risk.comission/slippage — dead config | **НИЗКИЙ сейчас** (значения совпадают), средний при изменении комиссии |
| 8 | backtester.py:1069 vs PG/YAML leverage | live lev=1 (PG) | `_lev` из несуществующего top-level `params` → 1.0 — только случайно | **СРЕДНИЙ**: смена leverage в конфиге не подхватится |
| 9 | engine/lsr_cross.py:32-33 vs PG | Python-бэктест trail 0.02/0.015; **live — PG 0.05/0.06** | YAML 0.05/0.06 = live (PG) ✓ | **СРЕДНИЙ (для сверки с Python-бэктестом)**: YAML ≠ Python-бэктест по trail, но = live |
| 10 | _run_lsr_mode:1177-1186 vs tick.py:253-265 | MTM adverse-only (только минус), с пирамидой | MTM знакопеременный, без пирамидального множителя у открытых позиций | **СРЕДНИЙ**: несопоставимые peak/DD; DD-stop нельзя повесить на эту кривую как есть |
| 11 | detect.py:77-81 vs _run_lsr_mode:1156 | 1 позиция на символ; пропуск закрытых <7д; лимит с pending | count-only (max_conc): повторный вход на символ возможен | **СРЕДНИЙ**: повторные/дублирующие входы на тот же символ |
| 12 | pg_state:122-157 vs strategy_engine | конфиг живёт в PG strategy_config | YAML-движок берёт конфиг из YAML/CLI, PG strategy_config не читает (в _run_lsr_mode) | **СРЕДНИЙ**: дрейф конфигурации между «что в PG» и «что в YAML» |
| 13 | actions.py:170-177 | Python-бэктест: entry 5m close | entry 1m close (ближе к live tick, но ≠ Python-бэктест) | **СРЕДНИЙ**: сверка YAML-бэктеста с Python-бэктестом даст разброс на маргинальных сделках |
| 14 | backtester.py:761-877 vs engine/lsr_cross.py:89-95 | pandas rolling std ddof=1 | популяционное std ddof=0 в движке; нет порогов ≥10 событий/≥2000 LSR | **НИЗКИЙ**: пограничные кроссы z |
| 15 | cli.py:275-279 | live loop в TQA-crypto работает | `paper` = TODO-заглушка | **БЛОКИРУЮЩИЙ**: пересадки как «переключения» не существует — нужна реализация live-контура |

---

## Вердикт

**Можно ли безопасно пересадить live 1:1 на YAML-движок сейчас? — НЕТ.**

Причины (в порядке критичности):
1. **sym_risk не применяется** в `_run_lsr_mode` (баг вложенности YAML) — каждая сделка с sym_risk≠1.0 будет просайзена неверно (проверено на ARB: −2.25 vs live −2.71).
2. **SOLUSDT не исключается** — движок будет входить в символ, который live принципиально не торгует.
3. **DD-stop 25% отсутствует** — при просадке >25% live принудительно закрывается и перестаёт входить; YAML-движок продолжит торговать.
4. **Нет live-контура** (cli `paper` — TODO): «пересадка» означает написание нового live-loop поверх strategy_engine с полным контрактом multi_state, а не замену конфига.
5. Вторичные: риск из CLI (default 0.15) вместо base_risk 0.08; MTM/DD-семантика; ремап ts и фильтры по границам; `--tf`-зависимость входа.

Хорошая новость: **exit-механика** (1m hi/lo, порядок pyr→sl→tp, филл по уровню, trail_lock, hold 120ч), **event-семантика триггера** и **trail-константы 0.05/0.06 (= PG, = live)** в YAML-движке уже воспроизведены верно. При исправлении пунктов ниже движок способен дать паритет с live.

## Рекомендуемые доработки в tqa_framework (НЕ выполнялись — только рекомендации)

1. **backtester.py `_run_lsr_mode`/`_rr`:** читать `sym_risk` из `raw['risk']['sym_risk']` и `leverage` из `raw['risk']['leverage']` (вместо несуществующих top-level `sym_risk`/`params`); риск = `risk_pct × sym_risk × lev`.
2. **`exclude.syms`:** применять в run() до/на этапе загрузки external-сигналов (и в LsrCrossBacktester — брать из YAML).
3. **DD-stop:** в `_run_lsr_mode` (и будущем live-контуре) реализовать `dd_stop_pct` (adverse-only MTM от peak; при превышении — закрыть все и не открывать новые). Опционально: параметр для backtest-режима.
4. **base_risk:** в LSR-режиме брать `raw['risk']['base_risk']`; CLI `--risk-pct` при `--strategy-yaml` валидировать/переопределять; сменить дефолт риск-pct или запретить запуск LSR без явного значения.
5. **Фильтр часов/DOW:** применять по исходному ts события (до ремапа), либо хранить оба ts; в `_run_lsr_mode` фиксировать требование `--tf 5` (валидировать и, при необходимости, ресемплировать 5m отдельно от tf).
6. **actions.py `_lsr_execute`:** брать comm/slip из config (cfg.get('comm'), cfg.get('slip')) вместо хардкода 0.0015.
7. **MTM:** в equity-цикле учитывать пирамидальный множитель для открытых позиций (pos_value с base_risk); согласовать семантику signed/adverse с DD-stop live.
8. **Live-контур:** реализовать `cli paper` (или отдельный раннер) поверх strategy_engine: detect → pending_signals → tick на 1m из CH/klines_1m, state в `strategies.multi_state` (eq_open, margin, trail_peak/trail_active, ts_close, balance-семантика, max_margin_ratio), запись в `multi_closed_trades`. Перед деплоем — светочный тест: **replay live-окна** (475→546.18→547.13→544.42; ARB −2.71) на YAML-движке с теми же конфигом и 1m-данными.
9. **Ограничения портфеля:** одна позиция на символ + пропуск символов, закрытых <7 дней (паритет с detect.py), лимит с учётом pending.
10. **Z-скоринг:** унифицировать ddof (решить: pandas ddof=1 как в Python) — пограничные кроссы.
11. **Сверка:** после фиксов прогнать YAML vs Python-бэктест за тот же период с одинаковыми константами (trail 0.05/0.06, exclude полный набор) — расхождение должно быть только в цене входа (5m vs 1m close).

---

## Приложение: как грузятся параметры live (ответ на вопрос «какой trail реально исполняется»)

Цепочка: `tick.py/detect.py → load_lsr_config(cur) (pg_state.py:122-157) → SELECT strategy_config FROM strategies.multi_state` → PG JSONB **перекрывает** defaults из engine/lsr_cross.py по каждому ключу (`if k in cfg: defaults[k] = cfg[k]`). В PG: `trail_act=0.05, trail_dist=0.06` → **live исполняет trail 5%/6%** (не 0.02/0.015 из engine). Проверено: обе trail-закрытия (APT, NEAR) вышли ровно на trail_lock=+2% уровне — консистентно с 0.05/0.06-trigger + lock. `strategies/lsr_cross/live/paper.py` (старый автономный папер с data/lsr_cross_state.json и импортом TRAIL_ACT/TRAIL_DIST из engine) — **legacy, в активном live-контуре не участвует** (активный = strategies/lsr_cross/detect.py + tick.py + PG multi_state, см. AGENTS.md).

*Аудит read-only: файлы TQA-crypto и tqa-framework читались, PG читался (SELECT), ничего не изменялось.*