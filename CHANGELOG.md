## [001] 2026-11-20
### Changed
- Тройной своп среда→четверг реализован (engine.py close-блок): `swap × (nights + triple_nights×2)`
- TRIZ-патчи (site-size, ATR-гейт, ratchet) удалены — отклонены по ROI/DD
- Спека расширена до 46 инструментов (forex_specs.py), починен QUOTE_MAP
- Champion обновлён: **+160.7%, DD 14.86%, 181 сделка** (risk 0.020, triple swap)
- Checkpoint: checkpoints/001-trend-sma-xau-limit-order-opt.md

## [20261120_fx_verdict] 2026-11-20
### Conclusion
- Протестированы 4 подходы (canal, cross-pair lag, momentum, усилители) на честном OOS
- Ни один не дал стабильного ROI>20%/год при M1 MDD<15%
- Canal: IS 2025 +32% → OOS 2026 −11% (развал)
- Lag eurgbp→gbpusd: спред съедает (222% от gross); честно +11.7%/5.6%
- Momentum USDJPY z3.0: OOS +34.6% но IS ~0 → перефит
- Вывод: FX intraday потолок ~10-15%/год при жёстком M1<15%; «сотни %» = перефит/артефакты
- Checkpoint: checkpoint/20261120_fx_verdict.md

## [20261120_ir_canal_rejected] 2026-11-20
- Canal mean-reversion отклонён: OOS 2026 развалился (GBPUSD −11%, EURUSD −16%)

## [20261120_canal] 2026-11-20
### Added
- Canal strategy (z-score mean-reversion) — новый подход для FOREX
- EURJPY 8H 5× +1671%/M1 13.1% ✅ (top find)
- EURJPY 6H 5× +1501%/12.8% ✅
- TF×hold sweep — full table above 95% ROI/MDD
- Checkpoint: checkpoint/20261120_ir_canal.md

### Results
- **Top single**: EURJPY 8H h6 r5× ROI +1671%, M1 MDD 13.1%, WR 59.7%, 380 trades
- **Portfolio**: EURJPY+USDJPY 4.5× ROI +119.8%/M1 14.5%
- CAGR ~+400%/год

### Fixed
- Старые script'ы: pnl double-count, w=48 lookahead, SL=1.5% too tight
- tf_hold_sweep.py: tuple access in SPECS

## [20261119] 2026-11-19
### Added
- IR strategy optimization: trailing SL code integration, ticker selection (audusd→audjpy)
- i-parameter sweep (12/24/36/48) — i=48 optimal
- Kanban-based systematic optimization (3 columns, 7 tasks)
- Checkpoint: checkpoint/20261119_ir_optimization.md

### Changed
- `strategies/ir/detect.py`: trailing stop now functional (peak tracking, pyramid limiting)
- `scripts/ir_pnl_tracker.py`: audusd → audjpy
- `scripts/ir_pnl_tracker_v2.py`: audusd → audjpy

### Results
- SUM ROI: +48.54% → +292.42% (+502% improvement)
- AVG MDD: +5.99% → +4.61% (-23% lower)
- ROI/MDD ratio: 2.03 → 9.05 (+346%)
- Trades: 878 → 2345 (+167%)