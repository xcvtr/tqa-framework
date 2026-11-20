# Чекпойнт 2026-11-17 — архитектура приведена к контракту (слои + 2 образа + гейт)

## Что сделано

**1. Слой решений вынесен из исполнителя.**
- Новый `strategies/trend_sma/position_manager.py` (208 → ~250 строк): `PositionManager.update` (SL, трейлинг arm/пик/откат, пирамидинг, scale-in, timeout, fixed TP), `size_position`, `check_portfolio_gates`.
- `broker.py` 328 → 148 строк: остались `Position` (состояние + пороговые properties), `check_liquidity`, `_close_market` (fill/slippage/комиссия/PnL) и делегат `update → self.pm.update`.
- Перенос **механический** (текст методов перемещён без правки формул; `self._close_market` → `self.executor._close_market`).
- `self_tester.py` и `runner.py` больше не содержат своих формул сайзинга и гейтов — вызывают `position_manager`.

**2. Инфраструктура по контракту «2 контейнера».**
- `docker/Dockerfile.detector` — только `tqa_f/connector/exchange_base.py`, `tqa_f/data`, `tqa_f/state`, `detect.py`, `data_provider.py`, `detector_cli.py`.
- `docker/Dockerfile.position` — всё остальное.
- `docker/compose.yml` — сервисы `detector` и `position`, креды через env с дефолтами, паролей в файле нет.
- Проверка изоляции слоя: `docker run --rm tqa-f:detector python -c "import tqa_f.executor"` → `ModuleNotFoundError` ✓.

**3. Один entrypoint, режим — флагом.**
- `scripts/detector_cli.py`: `check` (self-sufficiency, БД не трогает) и `scan --dry-run` (CH → `detect(bars, config)`).
- `scripts/position_cli.py`: `--mode test|paper --persist none|pg --connector mock|mt5`; логики внутри нет, только делегирование. `--persist pg` и `--connector mt5` честно `NotImplementedError` (не тихий pass).

**4. Тесты и гейт.**
- `tests/` — 7 тестов без сети: изоляция детектора, контракт исполнителя (ликвидность, SL через pm, пирамидинг +1 шаг, формула сайзинга), smoke CLI.
- `scripts/parity.sh` — прогон 28 тикеров и сверка с эталоном, печатает PASS/FAIL, exit 1 при расхождении.

## Гейт паритета (главное)
До и после рефакторинга — **бит-в-бит**:
- сделок `1377`
- ROI `1055.48%`
- MTM-DD `11.88%`
- WR `42.05% (579W/798L)`
- equity `2 310 966`

Прогоны: `/tmp/pm_after.log`, `/tmp/pm_after.json`; `bash scripts/parity.sh` → PASS.

## Контейнеры (проверено запуском)
- `tqa-f:detector` — `python scripts/detector_cli.py check` → `Self-sufficiency check passed`, exit 0.
- `tqa-f:position` — `python scripts/position_cli.py --mode test --tickers MN --start 2026-09-01 --end 2026-11-11` → 4 сделки, ROI −0.65%, DD 0.72% (исполнение внутри образа работает).

## Что осталось (не блокирует контракт слоёв)
1. `runner.py` (live-петля) всё ещё свой цикл: перевести на `position_manager` целиком + `--persist pg` (state-journal).
2. `tqa_f/executor/executor.py` (656 строк) — мёртвый дубль, никто не импортирует: удалить либо свести к `BrokerSim`.
3. PgState: `save_signal`/`insert_closed` для live не реализованы — `--persist pg` пока заглушка.
4. `check.py` в детектор-образе не проверяет отсутствие импорта `broker` (проверяется только executor вручную).

## Механизм (почему так, а не иначе)
- Код переносился текстом, а не переписывался: любая «улучшайзия» в слое решений немедленно ломает паритет 1377 сделок, а гейт ловит это только по числу сделок, не по каждой сделке.
- Детектор в отдельном образе без executor — единственная проверка, которая физически не даёт слоям слиться обратно.
- Делегирование clawcodex в этот раз не сработало: агент не может читать файлы проекта (Read/Bash возвращают пустоту), поэтому писал вслепую; его scaffold (docker/scripts/tests) принят только после того, как тесты и CLI были починены и прогнаны вручную.
