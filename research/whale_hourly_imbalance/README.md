# whale_hourly_imbalance (whale)

Теги: микроструктура, aggTrades, дисбаланс, крипта | **Статус: research** (не закрыт, не продвинут — кандидат на улучшение) | Таймфрейм: H1

## Тезис
Часовой дисбаланс агрессорных сделок на Binance USDT-M perpetus (aggTrades):
`imb = (buy_usd - sell_usd)/(buy_usd + sell_usd)`. |imb| > 0.3 = «кит давит» в одну сторону →
follow. Вход VWAP часа, выход VWAP следующего часа (hold 1ч), SL 5%. **Только maker** (taker 35bp убыточен).

## Данные
- Источник: `crypto.agg_trades_binance` (ClickHouse 10.0.0.60:8123, ReplacingMergeTree), сырые aggTrades.
- ~20Б строк, партиции по месяцу, ORDER BY (symbol, ts).
- Символы: топ-80 по USD-объёму (sum(qty*price) > 1M) за 30д окно до end, минус стаблы (USDCUSDT/FDUSDUSDT/TUSDUSDT/PAXGUSDT/EURUSDT/WBTCUSDT/USDPUSDT/DAIUSDT).
- Время: UTC-naive (тег +08 на CH — ложный, отбрасывать).

## Метрики (ЧЕСТНЫЙ M5-MTM, risk 3%, max_pos 5, comm/slip maker 5bp+5bp)

| Период | Роль | ROI | MDD | WR | PF | Calmar | N |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 2025 H1 | IS | +17.5% | 0.41% | 62.1% | 2.33 | 43 | 2573 |
| 2025 H2 | OOS | +25.3% | 1.74% | 57.9% | 1.87 | 14.6 | 4443 |
| 2026 YTD | OOS | +2.4% | 0.31% | 53.6% | 1.56 | 7.6 | 1139 |

### Risk sweep (M5-MTM, 2025 H1, N=2573)
| risk | ROI/6мес | MDD |
|:--:|:--:|:--:|
| 3% | +17.5% | 0.4% |
| 10% | +70.9% | 1.4% |
| 20% | +191.5% | 2.7% |
| 30% | +395.9% | 4.1% |
| 40% | +741.8% | 5.4% |

## Вердикт / ПОЧЕМУ не продвинут в live, но и не закрыт
- **Edge реален, но деградирует:** PF 2.33 → 1.87 → **1.56** (монотонный спад), ROI 2026 коллапсировал до +2.4% при остаточной PF. WR стабильно >53% → сигнал «кит давит» жив, но **монетизация сжимается** год от года.
- **Экономика на грани:** gross ~35bp. taker 35bp → в ноль (НЕ деплоить). maker ≤15bp — край есть, но лимитки в live сложнее.
- **Честность:** look-ahead снят (закрытый баг: `vwap.shift(-1)` на отфильтрованном фрейме давал фейк +282% → правильный hold-1ч-VWAP дал +17.5%). MDD считан на M5-MTM (worst-path по M5 lo/hi), не налоен почасовым экстремумом.

## Воспроизведение
- Прогон M5-MTM (IS/OOS): `python run_m5_mtm.py` (харнесс в этой папке); параметры: th=0.3, sl_pct=0.05, max_pos=5, comm=0.0005, risk=0.03, initial 1000.
- Прогон walk-forward: см. скилл research-brick, харнесс `/tmp/whale_oos.py` из history сессии.
- Бэктестер: `tqa_framework/backtesters/whale.py` (WhaleBacktester), зарегистрирован в backtesters/__init__.py, CLI `--backtester whale`. ⚠️ **кастомный источник (aggTrades) — PG shared.strategies НЕ применяется** (seed падает на unknown 'hourly_imbalance' metric); реестр = эта папка.
- Загрузка данных помесячная (partition-pruned 150GB скан → timeout обходится by toYYYYMM).

## Ограничения
- 6-мес IS + 9-мес OOS, крипта в 2025-26 — часть regime, не весь.
- Без slippage-adverse (5bp maker фиксир., реальный проскальзывание на лимитках может быть хуже).
- MDD на M5-MTM, не M1 (интра-бар риск мал т.к. вход/выход — пара VWAP, M5 vs M1 дал разницу 0.38→0.41%).
- Юниверс топ-80 — концентрация на крупных альткойнах.

## Поткрытия/артефакты (уроки)
- STABLES засоряют imbalance — всегда исключать.
- Топ-символы по USD-объёму, НЕ count() — новинки дают млн микров-тиков при нулевой ликвидности.
- 2025 H1 v1 +282% был look-ahead; v2 честный +17.5%.
- SL 5% ОБЯЗАТЕЛЕН — хвосты −187% без него.

## Связанное
- Чекпойнты в TQA-crypto: look-ahead-фикс, M5-MTM, risk sweep.
- Скилл: research-brick (этот формат).
- YAML: `whale_hourly_imbalance.strategy.yaml` (Status: research).