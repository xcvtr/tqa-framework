# Чекпойнт 2026-11-17 — state-journal подключён, второй бэктест удалён

## Что сделано

**1. `--persist pg` работает из движка** (`strategies/trend_sma/self_tester.py`).
- Флаги: `--persist none|pg`, `--pg-schema` (default `tqa_f_bt`), `--reset-state`.
- Пишет через `tqa_f.state.pg_state.PaperDB`: `multi_closed_trades` (по сделке) + `multi_state` (equity/peak/balance/positions).
- Границы: numpy-скаляры конвертируются в python-типы перед вставкой (`np.int64` psycopg2 не адаптирует — из-за этого молча терялись 18 из 20 сделок).
- `PaperDB.save/save_trade` больше **не глотают** ошибки: rollback + `log.exception` + raise. Раньше исключение съедалось в `log.error`, и журнал молча терял строки.
- Идемпотентность: `ON CONFLICT DO NOTHING` по `UNIQUE (symbol, direction, ts_open, entry_price)` — повторный прогон не дублирует (проверено: 20 строк до и после).
- `PaperDB.reset()` — очистка state для dev/paper-restart.

**2. Второй бэктест удалён** — `runner.py` 355 строк → 22 строки-обёртки над `self_tester.main`.
- Причина: он считал по своим параметрам (pyramid `0.3/0.01/2`, `go_knur=1.0`, `min_step=1.0  # TODO`), подавал детектору сырые M1-бары дня вместо 149 дневных и не использовался никем.
- Теперь `python -m strategies.trend_sma.runner …` = тот же движок, что и тестер; второй реализации в проекте нет.

**3. CLI:** `scripts/position_cli.py` пробрасывает `--persist/--pg-schema/--reset-state` в движок (заглушка `NotImplementedError` убрана). `--connector mt5` по-прежнему честная заглушка.

## Проверки (реальные прогоны)
- `pytest tests/` → **12 passed** (добавлены: runner = обёртка, наличие persist-флагов в CLI).
- `bash scripts/parity.sh` → **PASS**: 1377 / +1055.48% / 11.88% / 42.05% / 2 310 966.
- MN с записью в PG: `--persist pg --pg-schema tqa_f_bt --reset-state` → `20 trades`, `Persisted … equity 219,095`;
  в PG `count=20`, `sum(pnl_rub)=19 095.26` = PnL тестера; `multi_state`: equity `219 095.26`, peak `222 836.51`.
- Повторный прогон без `--reset-state` → всё ещё 20 строк (идемпотентно).
- В контейнере `tqa-f:position`: `--tickers MN --start 2026-09-01` → `4 trades`, `Persisted … equity 198 708`; в PG `4 строки / −1 292.10`, state equity `198 707.9`.
- Образ `tqa-f:position` пересобран (`build=0`).

## Состояние PG
Созданы схемы `tqa_f_bt` (полный MN-прогон) и `tqa_f_bt_cont` (проверка из контейнера). Прод-данные не затрагивались.

## Что осталось
1. **Live/paper-петля не написана.** Движок исторический: грузит весь период и идёт по M1-таймлайну. Для live нужно rolling-окно (последние N дней на каждый тик) и восстановление state из `multi_state` — иначе live ≠ bt по входам (это известная ловушка: bt видит фиксированный период, live — скользящее окно).
2. `--connector mt5` — заглушка, реального коннектора нет.
3. `detector_cli scan` работает только с `--dry-run` (без записи сигналов в PG).

## Механизм (почему так)
- Journal подключён к движку, а не к «раннеру», потому что единственная реализация исполнения теперь — движок; отдельный writer означал бы второй путь записи и второй источник расхождений.
- Ошибки записи сделаны громкими намеренно: журнал, который тихо теряет сделки, хуже отсутствия журнала — на нём строятся сверки live↔bt.
- `runner.py` сохранён как имя (совместимость CLI), но тело делегирует: меньше кода, нет дрейфа параметров.
