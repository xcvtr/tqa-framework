---
title: "LSR-CROSS: live-config YAML port + verification vs live PG"
checkpoint: 009-lsr-cross-live-yaml-verify
date: 2026-08-31
tags: [checkpoint, lsr-cross, crypto, yaml-port, verification]
---

# LSR-CROSS live YAML port — ВЕРИФИКАЦИЯ: НЕ воспроизводится

## Статус
- ✅ Live-конфиг закодирован в YAML: `TQA-crypto/strategies/lsr_cross/config.yaml` (источник истины — PG multi_state id=1)
- ❌ YAML-движок `_run_lsr_mode` НЕ воспроизводит live equity ($544.42) — расхождение по всем закрытым сделкам

## Ground truth (из PG 10.0.0.60 crypto, не из примеров)
- Equity-таймлайн (закрытые): 475 → APT +71.18 → 546.18 → NEAR +0.95 → 547.13 → ARB −2.71 → **544.42** (= PG equity)
- 2 open: OPUSDT SHORT @0.0930 (eq_open 475), DOGEUSDT SHORT @0.08472 (eq_open 546.18)
- Live config: th=2.0, z_window=720, base_risk=0.08, leverage=1, max_pos=6, sl=5%, tp=18%, comm=0.001, slip=0.0005, trail_act=0.05, trail_dist=0.06, pyr_trigger=0.05, pyr_add=0.5, timeout=120, excl_hours=[11,22,23], excl_dow=[2], excl_syms=[SOLUSDT]
- sym_risk: ARB=1.2, ADA=1.3, OP=1.1, AVAX=1.05, NEAR=0.9, APT=0.9, прочие=1.0
- Сигналы: пишутся в `multi_state.pending_signals` (не в multi_signals — там 0 строк)

## Формулы PnL (ключевое расхождение)
- **Live** (tick.py:238): `pnl_usd = base_eq * risk * lev * pnl_pct`, где `risk = base_risk * sym_risk[sym]`
- **YAML** (`backtester._run_lsr_mode`), строка 1207: `pnl_dollars = eq * self.risk_pct * pnl_eff`
  → использует плоский `risk_pct=0.08`, **без sym_risk-множителя** и **без leverage**

### Проверка (ARB = единственная post-fix сделка, воспроизводится точно)
- ARB live: 547.13 * 0.08 * 1.2 * (−0.0515) = **−2.71** ✓ (= фактический)
- ARB yaml: 547.13 * 0.08 * (−0.0515) = **−2.25** ↛ 17% занижение (только из-за sym_risk)
- NEAR live 0.73–0.95 vs yaml 0.81; APT — до сброса (др. риск/леверидж), невалиден для сверки

## Причины (по приоритету, с кодом)
1. **sym_risk не применяется** — `backtester.py:1207,1175` `risk_pct` плоский; tick.py:160 `risk=base_risk*sym_risk`. Расхождение 0.9–1.3×.
2. **Цена входа** — YAML входит по следующему 5m close (actions.py:167 `px_c5[ei]`); live — next tick last_close (1m). Редкие сигналы → разная точка входа.
3. **Сигнальный контур** — live детект→pending_signals→tick; YAML `_run_lsr_mode` ждёт `all_external_signals`, взятых из бара/внешнего источника. Перенос live-контура ≠ простой реплей.

## Вывод
Live-конфиг зафиксирован верно. Но **YAML-движок сейчас не 1:1 с live tick.py** — реплей live-окна даст чужие сделки/equity. Нужен фикс движка (sym_risk + leverage в sizing + вход по 1m), либо полный перенос живого контура (detect→pending→tick на YAML-движке). Бэктестер не создавать.

## Файлы
- `TQA-crypto/strategies/lsr_cross/config.yaml` — live YAML (новый)
- `tqa_framework/engine/backtester.py:1025 _run_lsr_mode, 1207` — плоский risk_pct
- `tqa_framework/strategy_engine/actions.py:127 _lsr_execute` — вход по 5m close
- `TQA-crypto/strategies/lsr_cross/tick.py:160,238` — live math: sym_risk, lev