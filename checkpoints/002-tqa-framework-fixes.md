---
title: "tqa-framework fixes — cash_equity, summary_id, deterministic end_time, MT5 chart"
checkpoint: 2
date: 2026-07-27
tags: [checkpoint, tqa-framework, framework]
---

# Checkpoint 002 — tqa-framework fixes

## Что изменилось

### Исправлено: cash_equity (Balance) + summary_id
- Добавлена колонка `cash_equity` в `backtest.equity_curve` — отдельная линия Balance (только реализованная PnL)
- Добавлена колонка `summary_id` — equity точки привязаны к конкретному запуску
- Старые перемешанные данные (8033 строки) удалены
- `save_summary()` теперь возвращает id через `RETURNING id`

### Исправлено: недетерминированный CH запрос
- `load_m1_from_ch()` теперь принимает `end_time` — фиксированное окончание окна
- Бэктестер определяет последний бар в CH один раз перед циклом тикеров
- Результаты бэктеста теперь воспроизводимы — два запуска с одинаковыми параметрами дают идентичный результат

### Изменён график equity_to_fig
- MT5-стиль: Balance (зелёная, ступенчатая `shape="hvh"`) поверх Equity (синяя)
- Equity рисуется первой (задний план), Balance второй (передний план)
- Стартовый капитал — серый пунктир
- Аннотация с метриками в правом верхнем углу
- Убран drawdown subplot, peak line и fill

## Файлы
- `engine/backtester.py` — cash_equity, summary_id, фиксация end_time через CH max(bt)
- `engine/pg_state.py` — колонки cash_equity, summary_id; save_summary → RETURNING id
- `engine/detect.py` — end_time параметр, AND bt <= end_time
- `scripts/bt_report.py` — load_equity по summary_id, MT5-стиль графика

## Воспроизводимость

```bash
# Первый запуск
python -m engine.cli --ch-db moex backtest \
  --tickers MIX --strategy test_ma --tf 60 --days 90 \
  --params '{"fast_ma":10,"slow_ma":40,"sl_pct":0.02,"tp_pct":0.04}'
# → +213.16%, 9534 сделок, MDD 0.05%

# Второй запуск (тот же результат)
→ +213.16%, 9534 сделок, MDD 0.05%
```
