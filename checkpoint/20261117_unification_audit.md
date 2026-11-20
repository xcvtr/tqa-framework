# Чекпойнт 2026-11-17 — унификация test/paper + аудит по скиллу

## Что сделано (коммиты `9c296a6`, `fe8b025`)

**1. Один движок решений.**
`strategies/trend_sma/engine.py` — `MarketData`/`State`/`Config` + `run_days()`/`force_close()`/`mtm_mdd()`.
`self_tester.py` (история) и `live.py` (paper/replay) — только драйверы: грузят данные и зовут `run_days`.
Тест `tests/test_single_mechanism.py` запрещает появление `detect/сайзинг/трейлинг` в драйверах.

**2. Paper-петля.**
- `position_cli --mode paper` теперь действительно запускает `live.paper()` (раньше молча гонял бэктест — расхождение с планом).
- `LiveDB`: старт поднимает `equity/peak/positions` из `multi_state` (позиция сериализуется в JSONB и восстанавливается), закрытые сделки пишутся в `multi_closed_trades`, save state — не чаще `--save-every` (не per-bar, как требует skill).
- `--start` ограничивает дату первых входов (иначе при отставании CH от календаря папер не торговал вообще).

**3. Слой данных вынесен.** `tqa_f/data/bars.py` (`msk_date/group_days/resample5`) — без импортов executor, поэтому доступен образу детектора. `engine.py` импортирует оттуда же.

**4. Detector CLI стал рабочим.** `scan --as-of YYYY-MM-DD` строит ровно тот вход, что движок (149 закрытых дневных + формирующийся 5м-бар), пишет сигналы через `PGState.save_signal` или `--dry-run`. `check` дополнительно запрещает импорт `broker`/`position_manager` внутри детектора.

**5. Попутные баги.**
- `data_provider`: `end=None` падал (`timezone.utc - timedelta`).
- `live.replay`: история не двигалась вперёд → detect видел дыру в днях и считал другие сделки (4 вместо 6).

## Проверки (реальные прогоны)
- `pytest tests/` → **18 passed**
- `scripts/parity.sh` → **PASS** 1377 / +1055.48% / 11.88% / 42.05%
- replay 2026-10-20→11-13 (MN,SG) → **6 сделок / −1 738.91 ₽** = бэктест бит-в-бит; PG-журнал: 6 строк / −1 738.91; state equity 198 261
- paper-петля запускается: «State restored: equity=200,000», реплей дня 11-13, state пишется в `tqa_f_paper.multi_state`
- оба образа пересобраны; `detector_cli check` внутри образа → passed; `scan MN --as-of 2026-11-13 --dry-run` внутри образа → 1 сигнал

## Закрыто после аудита (пункты 1 и 2)
1. **Connector подключён.** `tqa_f/connector/exchange_mock.py` реализует `ExchangeBase`; `BrokerSim(connector=…)` спрашивает филл-цену у коннектора. Без коннектора поведение бит-в-бит прежнее (проверено: 6 сделок / −1 738.91 ₽ и без него, и с `--connector mock`).
2. **Детектор отделён.** `detector_cli scan --from --to --schema` пишет `pending_signals`; движок в режиме `--signals pg` берёт сигналы через `PGSignalSource` и НЕ импортирует `detect` (это проверяет тест `test_pg_mode_never_imports_detector`). Проверено: 42 сигнала за 24 дня → exec даёт те же 6 сделок / −1 738.91 ₽.

## Отклонения от скилла (не закрыты)
1. **MT5-коннектор не реализован** — `--connector mt5` = NotImplemented. Слот готов (`ExchangeBase` + `fill_price` в BrokerSim), реализации нет.
2. `tqa_f/position/` пуст — логика позиции живёт в `strategies/trend_sma/` (для одной стратегии допустимо, для второй придётся выносить).
3. Детектор и исполнитель остаются в одном образе сборкой: разделение сделано по данным (сигналы в PG), но `Dockerfile.position` по-прежнему содержит `detect.py`.

## Механизм (почему так)
- Реплей текущего дня из day-start snapshot вместо инкрементального шага: одна реализация решений, нет второго пути, состояние детерминировано воспроизводимо. Цена — O(день) работы на тик (≈900 баров), для 28 тикеров приемлемо.
- Save state раз в N секунд: коммит на каждый M1-бар превращает годовой папер в минуты fsync (прямой пункт скилла).
- Data-хелперы в `tqa_f/data`: детектор обязан быть self-sufficient, а engine — не тащить их копию.
