---
title: "Canal/Lag/Momentum — FX final verdict: нет стабильного OOS edge >20%/год при M1<15%"
checkpoint: "20261120_fx_verdict"
date: 2026-11-20
tags: [checkpoint, tqa-f, fx, oos, verdict, rejected]
---

# FX final verdict — все подходы протестированы, потолок ROI при M1<15% ~10-15%/год

## Бэкграунд
Долгий разбор (полгода 2025 = IS, полный 2026 = OOS, честный M1 MTM MDD, spread AlfaForex,
вход на next-bar = без lookahead). Протестированы 4 подходы. Ни один не дал стабильного
>20%/год при M1 MDD<15% и честном OOS.

## 1. Canal (z-score mean-reversion) — ОТКЛОНЁН
- IS 2025: GBPUSD 8H h6 r5 +32.0%/7.8% (ratio 4.09), EURUSD 6H h2 r5 +27.7%/10.7%
- OOS 2026: GBPUSD −11.0%/18.3%, EURUSD −16.7%/23.9% → развалился
- Причина: реверсия на 4-8H барах зависит от трендового режима; в 2026 тренды сильнее.

## 2. eurgbp→gbpusd cross-pair lag — ОТКЛОНЁН (спред убьёт)
- z≥2.0, NY session (12-20 UTC), вход next-bar, hold 3×30m
- Честно (без lookahead): OOS +11.7%/5.6% (risk 15×), IS +6.3%/8.3%
- Спред съедает: gross +143.5pt / spread cost +319.2pt = 222% от gross. Средний move 0.94pt vs spread 2.1pt.
- z-система отказалась усиливаться (пирамида не срабатывает на коротком move).

## 3. Momentum (большие z, продолжение) — 90% ОТКЛОНЁН
- USDJPY z3.0 momentum H4: OOS +34.6%/2.8% (ratio 12) — ВЫГЛЯДИТ отлично
- Но IS 2025: ~0% → перефит на 2026-window, не стабилен.
- USDJPY z2.5 h2 стабилен (IS+12.6/4.9, OOS+9.6/8.7) но ROI скромный, сделок мало (59/10.5mo).

## 4. усилители (pyramid, scale-in, trailing, compound) — НЕ ПОМОГАЮТ
- С/без идентичны (ROI, M1, WR те же). Пирамида не успевает на hold 2-6, trailing не догоняет.
- Компаунд: M1 растёт, ratio падает. При старте $500 компаунд невыгоден.

## УРОК / ПАРАДИГМА
- **Проверять IS И OOS, не только один окно** — любой "raycast" на одном периоде = перефит.
- **WR<55% на IS = красный флаг** (монетка, выживает только leverage).
- **Спред — главный враг small-move стратегий**: нужен move 3-5× spread на сделку.
- **Тик/пирамида/трейл не создают edge**, только усиливают/ухудшают existing + комиссия.
- **FX intraday mean-reversion/momentum при M1<15%: потолок ~10-15%/год CAGR.** "Сотни % в год"
  зафиксированы только историей/перефитом (double-count MDD, lookahead w=48, compund).
- ROC-CAP на FX при жёстком MDD — реальная граница, честная математика ROI~risk×edge.

## ЛУЧШЕЕ (честное, OOS-стабильное)
- **USDJPY momentum z2.5 h2**: IS +12.6%/4.9%, OOS +9.6%/8.7% (ratio ~1.1-2.6). Не торгуемо как единственное — ROI мал.

## Файлы (scratch)
- canal_sweep_full.py, canal_m1.py, canal_amp2.py, canal_m15_pyr.py, canal_6h_m15pyr.py,
  canal_trail_compound.py, canal_m5_pyr.py, canal_intra_sweep.py, canal_compound_m60.py,
  canal_2026.py, lag_test.py* (2-4), lag_amp.py, lag_spread_cost.py, bigmove_scan.py, momentum_oos.py