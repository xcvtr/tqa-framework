# Checkpoint 005 — LSR-CROSS no-excludes verified

## Что сделано
- **CLI-верификация** z=1.5, no-excludes, YAML-дефолты — 5 полных прогонов по 11 тикерам 3y
- **YAML в PG:** excludes убраны (hours=[], dow=[], syms=[])
- **Чекпойнт запушен** (4f7f018)

## Результаты (11 тикеров crypto, 3y, MTM DD на M1)

| Конфиг | Return | DD | Trades | Calmar |
|--------|--------|----|--------|--------|
| Baseline (risk=0.08, excl=[22,23]) | +238.6% | 11.85% | 1,173 | 20.1 |
| z=1.5 (risk=0.08) | +249.2% | 18.35% | 1,614 | 13.6 |
| YAML-дефолты [11,22,23]+dow[2] (risk=0.08) | +172.8% | 11.85% | 1,026 | 14.6 |
| **No-excludes (risk=0.08)** | **+287.7%** | **11.22%** | **1,319** | **25.6** |
| **No-excludes + risk=0.12** | **+640.8%** | **16.42%** | **1,319** | **39.0** |

## Вывод
1. **Excludes ВРЕДЯТ** — +20% ROI при той же DD
2. **z=1.5 не стоит** — +10% ROI за +55% DD
3. **Лучшая конфигурация:** no-excludes + risk=0.12 → 640%/DD 16.4%

## Файлы
- `tqa_framework/backtesters/lsr_cross.py` — патч: z_score/exclude/params из strategy_params
- `tqa_framework/engine/backtester.py` — load_lsr_data(z_score_threshold)
- `tqa_framework/engine/cli.py` — routing `--strategy-yaml lsr_cross` → LsrCrossBacktester

## Ограничения аудита
- Все прогоны **in-sample** (3y целиком). Walk-forward не сделан
- Потикерный разбор не сделан — может 1-2 тикера тянут весь результат
- Погодовая разбивка не сделана — возможно 2023 даёт весь профит