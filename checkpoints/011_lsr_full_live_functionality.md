# Checkpoint 011 — LSR-CROSS: полный live-функционал в YAML движке (53120a9)

**Дата:** 2026-08-31
**Статус:** ✅ DONE, запушено `53120a9` (содержит ранее закоммиченный 0a4fa7c)

## Цель
Воспроизвести живой paper trader LSR-CROSS 1:1 в YAML-движке tqa-framework для безопасной пересадки.

## Сделано (7 доработок из gap-аудита docs/lsr_live_migration_audit.md)
1. **sym_risk/leverage** из `risk.sym_risk` / `risk.leverage` (был баг: читал top-level → None)
2. **exclude.syms** (SOLUSDT) фильтр на прекомпуте
3. **DD-stop 25%** (adverse-only MTM, закрыть все + блок входов, reason='dd_stop')
4. **base_risk** из YAML (0.08) переопределяет CLI --risk-pct (был дефолт 0.15)
5. **Фильтр часов/DOW** по исходному ts события (не ремапленного)
6. **comm/slip** из `risk.comission/slippage` (был хардкод 0.0015)
7. **MTM с пирамидой** (pos_value × base_risk) + 1 позиция/символ + skip <7д

## Верификация
- pytest: **75 passed**
- Replay ARBUSDT: **implied risk $96.00 = 1000 × 0.08 × 1.2** — base_risk/sym_risk применены точно
- End-to-end `_run_lsr_mode` без падений, summary/trades генерируются
- Ранняя сверка (ARB −2.69/−2.71, NEAR $1.09/$0.95) сохранена

## Ground truth (live PG), который движок теперь воспроизводит
risk=base_risk(0.08)×sym_risk×lev(1); trail 0.05/0.06 (PG, не 0.02/0.015); exclude {SOLUSDT},{11,22,23},{2}; DD-stop 25%; max_margin_ratio 0.8; max_pos 6; event-trigger z±2.0; comm 0.001+slip 0.0005. Live equity 544.42/2 pos.

## Осталось / следующий шаг
- **t_78b0a937**: реализовать live-контур `cli paper` (detect→pending_signals→tick на 1m из CH, state в `strategies.multi_state`, запись `multi_closed_trades`, replay 475→544.42, ARB −2.71). Запущен на канбан.
- Отложено: z-скоринг ddof (низкий приоритет)

## Файлы
- `tqa_framework/engine/backtester.py` (_run_lsr_mode)
- `tqa_framework/strategy_engine/actions.py` (_lsr_execute comm/slip)
- `docs/lsr_live_full_functionality.md` — отчёт реализации
- `docs/lsr_live_migration_audit.md` — gap-аудит
- `checkpoints/009-lsr-cross-live-yaml-verify.md` — ранняя сверка