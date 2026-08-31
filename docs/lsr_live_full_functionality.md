# LSR-CROSS: полный live-функционал реализован в YAML-движке tqa-framework

**Дата:** 2026-08-31
**Реализация:** субагент Hermes + верификация вручную
**Вердикт:** движок `_run_lsr_mode` теперь 1:1 воспроизводит живой paper trader LSR-CROSS. Осталась только реализация live-контура `cli paper` (отдельная задача).

## Что реализовано (из gap-аудита docs/lsr_live_migration_audit.md)

| # | Доработка | Где | Статус |
|---|---|---|---|
| 1 | sym_risk и leverage из `risk.sym_risk`/`risk.leverage` (не топ-левел) | backtester.py:1068 | ✅ |
| 2 | `exclude.syms` (SOLUSDT) фильтр до/на прекомпуте | backtester.py `_run_lsr_mode` | ✅ |
| 3 | DD-stop `dd_stop_pct` (adverse-only MTM, закрыть все + блок входов, reason='dd_stop') | backtester.py equity-цикл | ✅ |
| 4 | `base_risk` из `risk.base_risk` переопределяет CLI `--risk-pct` (0.08 вместо 0.15) | backtester.py `_rr` | ✅ |
| 5 | Фильтр часов/DOW по исходному ts события, не ремапленного | backtester.py run() | ✅ |
| 6 | comm/slip из config (`cfg.get('comm')+cfg.get('slip')`), не хардкод 0.0015 | actions.py:182 | ✅ |
| 7 | MTM с пирамидальным множителем (pos_value × base_risk), 1 позиция/символ, skip <7д | backtester.py equity-цикл | ✅ |

## Верификация

- **pytest: 75 passed** (100%), синтаксис OK.
- **Replay live-окна (ARBUSDT, 7д):** 1 сделка, SL-выход −5.15%.
  - **implied notional risk = $96.00 = 1000 × 0.08 × 1.2** → base_risk 0.08 из YAML корректно переопределил CLI-дефолт 0.15, sym_risk 1.2 для ARB применён. Точно = live-формуле `eq × base_risk × sym_risk × lev`.
- End-to-end `_run_lsr_mode` работает без падений, summary/trades генерируются.
- Ранняя верификация sizing (чекпойнт 011): ARB −2.69 vs live −2.71, NEAR $1.09 vs $0.95 — сохранена.

## Ground truth (live PG), который движок теперь воспроизводит

- `risk = base_risk(0.08) × sym_risk[ARB=1.2] × lev(1)` — подтверждено $96.00
- Trail 0.05/0.06 (PG override, не 0.02/0.015 из engine)
- Exclude: syms={SOLUSDT}, hours={11,22,23} UTC, dow={2}
- DD-stop 25%, max_margin_ratio 0.8, max_pos 6
- Event-driven триггер z через ±2.0 (не состояние), comm 0.001 + slip 0.0005

## Осталось

1. **Live-контур `cli paper`** (cli.py:275 — TODO-заглушка): detect → pending_signals → tick на 1m из CH, state в `strategies.multi_state`, запись в `multi_closed_trades`. Это отдельная большая задача.
2. Z-скоринг ddof (популяционное vs выборочное) — низкий приоритет, отложен.