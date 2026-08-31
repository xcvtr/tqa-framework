---
title: "LSR-CROSS: risk sweep, сотни %/год при DD≤20%, CAGR 116%/год"
checkpoint: 007
date: 2026-08-31
tags: [checkpoint, tqa-framework, lsr-cross, risk-sweep, crypto]
---

# 007 LSR-CROSS: risk sweep, CAGR 116%/год при DD≤20%

## Что сделано

1. **Risk sweep** 0.08-0.30 шаг 0.02, 12 значений, MTM MDD на M1
2. **Лучший по CAGR @ DD≤20%**: risk=0.14 → **+916.5%/18.92% DD/CAGR 116.6%/год**
3. **Walk-forward подтверждён**: Train +191.4%/17.80%, Test +418.6%/18.92% — OOS выживает
4. **Реинвест**: risk_pct от текущего equity (дробное Келли). Да, реинвестирует.

## Ключевые метрики (risk=0.14, no-excl, equity=1000)

| Период | Return | DD | CAGR | Trades | Calmar |
|--------|--------|----|------|--------|--------|
| Full 3y | **+916.5%** | 18.92% | **116.6%/yr** | 1319 | 48.4 |
| Train 2024 | +191.4% | 17.80% | 191.4%/yr | 728 | 10.8 |
| Test 2025-26 | +418.6% | 18.92% | 174.5%/yr | 603 | 22.1 |
| No-excl @0.08 | +287.7% | 11.22% | 56.8%/yr | 1319 | 25.6 |
| No-excl @0.12 | +640.8% | 16.42% | 94.7%/yr | 1319 | 39.0 |

## Risk sweep таблица

```text
 risk |     return |      MDD | trades |   Calmar
------+------------+----------+--------+----------
 0.08 |  +287.74% |  11.22% |  1319 |   25.63
 0.10 |  +437.29% |  13.86% |  1319 |   31.56
 0.12 |  +640.84% |  16.42% |  1319 |   39.02
 0.14 |  +916.51% |  18.92% |  1319 |   48.44 ← best DD≤20
 0.16 | +1287.99% |  21.36% |  1319 |   60.30
 0.18 | +1786.11% |  23.73% |  1319 |   75.26
 0.20 | +2450.76% |  26.04% |  1319 |   94.10
 0.22 | +3333.28% |  28.30% |  1319 |  117.80
 0.24 | +4499.39% |  30.49% |  1319 |  147.58
 0.26 | +6032.74% |  32.62% |  1319 |  184.91
 0.28 | +8039.29% |  34.70% |  1319 |  231.65
 0.30 |+10652.44% |  36.73% |  1319 |  290.03
```

## Файлы

- `scripts/sweep_risk_lsr.py` — скрипт свипа (создан)
- `checkpoints/007-lsr-cross-risk-sweep-116pct-cagr.md` — этот чекпойнт
- `tqa_framework/backtesters/lsr_cross.py` — LsrCrossBacktester
- PG `shared.strategies.lsr_cross` — no-excludes YAML (пока risk=0.08)

## Продолжение

Финальная конфигурация для деплоя: **risk=0.14, no-excludes, z=2.0, trail_act=0.04/trail_dist=0.03/trail_lock=0.02, PYTHON LsrCrossBacktester**.

Потенциальные улучшения:
- Не YAML, а Python LsrCrossBacktester (YAML не может динамический risk)
- Возможно per-ticker risk scaling (ADA risk=0.10, BTC risk=0.18)
- Если нужен CAGR 200%+/год — поднять DD-лимит до 25-30% и взять risk=0.20+