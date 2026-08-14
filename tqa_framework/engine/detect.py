"""Detect — общий framework для поиска сигналов.

Загрузка M1 из CH → ресемпл → detect strategy → dedup → trend filter.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

import requests

import json

from tqa_framework.engine.exchange_base import Signal


_CH_URL = os.environ.get("CH_URL", "http://10.0.0.60:8123")


def load_m1_from_ch(
    symbol: str,
    hours: int,
    host: str = "",
    db: str = "",
    end_time: str = "",
    source: str = "bars",
) -> list[dict]:
    """Загрузить M1 бары из ClickHouse.

    Поддерживает три схемы:
      - moex:      таблица moex.bars (ticker, bt, opn, hi, lo, prc, vol)
      - forex:     таблица forex.bars (symbol, time, open, high, low, close, vol)
      - mt5_continuous: таблица moex.mt5_continuous (ticker, bt, opn, hi, lo, prc, vol)
        + опционально day_net из moex.futoi (для OI-стратегии)

    Args:
        symbol: Тикер (GD, GZ, EURUSD и т.д.)
        hours: За сколько часов до end_time загрузить
        host: CH URL (из CH_URL env или по умолчанию)
        db: БД (moex, forex)
        end_time: Фиксированное окончание (ISO). Если пусто — now()
        source: 'bars' | 'mt5_continuous' — откуда читать бары

    Returns:
        list[dict] с ключами: ts, open, high, low, close, volume
    """
    url = host or _CH_URL
    if not db:
        raise ValueError("db обязателен: 'moex' или 'forex'")

    end_condition = f"'{end_time}'" if end_time else "now()"

    if db == "forex":
        # forex.bars: symbol, time, open, high, low, close, vol
        # end_time строка без tz → конвертируем в DateTime64 с явной таймзоной
        if end_time:
            end_ts = f"toDateTime64('{end_time}', 3, 'UTC')"
        else:
            end_ts = "now()"
        query = f"""
        SELECT
            toTimeZone(time, 'UTC') as ts,
            open,
            high,
            low,
            close,
            vol as volume
        FROM forex.bars
        WHERE symbol = '{symbol}'
          AND time >= {end_ts} - INTERVAL {hours} HOUR
          AND time <= {end_ts}
        ORDER BY time
        FORMAT JSONEachRow
        """
    else:
        # moex.bars: ticker, bt, opn, hi, lo, prc, vol
        # или moex.mt5_continuous (source='mt5_continuous') — та же схема колонок
        tbl = f"{db}.mt5_continuous" if source == "mt5_continuous" else f"{db}.bars"
        query = f"""
        SELECT
            bt as ts,
            opn as open,
            hi as high,
            lo as low,
            prc as close,
            vol as volume
        FROM {tbl}
        WHERE ticker = '{symbol}'
          AND bt >= {end_condition} - INTERVAL {hours} HOUR
          AND bt <= {end_condition}
        ORDER BY bt
        FORMAT JSONEachRow
        """

    resp = requests.get(
        url,
        params={"query": query.strip()},
        timeout=30,
    )
    resp.raise_for_status()

    bars = []
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        row = json.loads(line)
        bars.append({
            "ts": row["ts"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
        })

    # Для OI: подгрузить day_net (дневной дисбаланс физлиц из moex.futoi)
    # День начинается в 07:00 UTC (граница IRK-дня, как в бэктесте/live).
    # Присваиваем day_net каждому бару (последний известный на момент бара).
    if source == "mt5_continuous" and bars:
        try:
            import requests as _r
            daynet_q = f"""
                SELECT toUnixTimestamp(toDateTime(bt)) ts, buy_fiz, sell_fiz, buy_yur, sell_yur
                FROM {db}.futoi
                WHERE ticker = '{symbol}'
                ORDER BY bt
                FORMAT JSONEachRow
            """
            dn_resp = _r.get(url, params={"query": daynet_q}, timeout=30)
            dn_resp.raise_for_status()
            dn_rows = [json.loads(l) for l in dn_resp.text.strip().split("\n") if l.strip()]
            # day_start = первая запись IRK-дня (07:00 UTC), day_net = (cur-start)/total
            from collections import defaultdict
            day_start = {}
            dn_by_ts = {}
            for r in dn_rows:
                ts = int(r["ts"])
                d = (ts - 7 * 3600) // 86400
                if d not in day_start:
                    day_start[d] = int(r["buy_fiz"]) - int(r["sell_fiz"])
                total = int(r["buy_fiz"]) + int(r["sell_fiz"]) + int(r["buy_yur"]) + int(r["sell_yur"])
                if total > 0:
                    dn_by_ts[ts] = (int(r["buy_fiz"]) - int(r["sell_fiz"]) - day_start[d]) / total * 100.0

            # Присвоить каждому бару последний day_net ≤ ts бара
            dn_ts_sorted = sorted(dn_by_ts.keys())
            import bisect as _bisect
            # Ролл-гэп: скачок >3% между соседними M1 = смена контракта ALLFUT.
            # Помечаем бары после гэпа (roll_gap=1) — detect не должен входить через ролл.
            prev_close = None
            for b in bars:
                bts = _parse_ts(b["ts"])
                i = _bisect.bisect_right(dn_ts_sorted, bts) - 1
                if i >= 0:
                    b["day_net"] = dn_by_ts[dn_ts_sorted[i]]
                if prev_close and prev_close > 0:
                    chg = abs(b["close"] / prev_close - 1)
                    if chg > 0.03:  # ролл-гэп (>3% за 1 мин — контракт сменился)
                        b["roll_gap"] = 1
                prev_close = b["close"]
        except Exception:
            pass  # day_net опционален — без него OI просто не даст сигнал
    return bars


def _parse_ts(ts):
    """unix или ISO → unix (для day_net маппинга)."""
    if isinstance(ts, (int, float)):
        return int(ts)
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def resample_bars(bars: list[dict], tf_minutes: int) -> list[dict]:
    """Ресемпл M1 → N-минутные бары (OHLCV).

    Args:
        bars: M1 бары с ключами ts, open, high, low, close, volume
        tf_minutes: Таймфрейм в минутах (3, 5, 10, 15, 30, 60)

    Returns:
        Ресемпленные бары
    """
    if not bars:
        return []

    import math

    result = []
    chunk: list[dict] = []
    last_ts = None

    for bar in bars:
        ts = datetime.fromisoformat(bar["ts"])
        # Нормализовать ts к началу TF-окна
        minutes = (ts.hour * 60 + ts.minute) // tf_minutes * tf_minutes
        window_start = ts.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)
        # минуты превращаем в окна — проще сгруппировать
        chunk.append(bar)

    # Альтернатива: группировка
    groups: dict[str, list[dict]] = {}
    for bar in bars:
        ts = bar["ts"]
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts)
        else:
            dt = ts
        # Округлить до TF
        epoch = int(dt.timestamp())
        window = epoch // (tf_minutes * 60) * (tf_minutes * 60)
        key = datetime.fromtimestamp(window, tz=dt.tzinfo).isoformat()
        groups.setdefault(key, []).append(bar)

    for key in sorted(groups):
        chunk = groups[key]
        res = {
            "ts": key,
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        }
        # day_net: последний M1 в окне (для OI-стратегии)
        dn = chunk[-1].get("day_net")
        if dn is not None:
            res["day_net"] = dn
        # roll_gap: если любой бар окна — ролл, помечаем реземпленный
        if any(b.get("roll_gap") for b in chunk):
            res["roll_gap"] = 1
        result.append(res)

    return result


def dedup_signals(
    new: list[Signal],
    existing: list[Signal],
    existing_trades: list[dict],
    include_trades: bool = True,
) -> list[Signal]:
    """Dedup: исключить уже торгованные сигналы.

    Сравнение по (symbol + direction + price_bucket).
    price_bucket = round(price, 1) — для MOEX фьючерсов.

    include_trades=False — не учитывать закрытые сделки (для OI: сигнал
    повторяется много раз в год, нужен вход после каждого закрытия).
    """
    seen = set()
    for s in existing:
        seen.add((s.symbol, s.direction, round(s.price, 1)))
    if include_trades:
        for t in existing_trades:
            seen.add((t.get("symbol"), t.get("direction"),
                      round(t.get("entry_price", 0), 1)))

    result = []
    for s in new:
        key = (s.symbol, s.direction, round(s.price, 1))
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def trend_filter(trend: str, direction: str, profitable: bool) -> bool:
    """Trend filter: блокирует контр-тренд после прибыльной сделки."""
    from tqa_framework.engine.risk import trend_filter as _tf
    return _tf(trend, direction, profitable)


def _sma_trend(prices: list[float]) -> str:
    """Определить тренд по SMA 50."""
    from tqa_framework.engine.risk import sma_50_trend
    return sma_50_trend(prices)


def run_detect(
    strategy_detect_fn: Callable,
    symbol: str,
    tf_minutes: int,
    hours: int = 48,
    source: str = "ch",
    ch_host: str = "",
    ch_db: str = "",
) -> list[Signal]:
    """Общий detect pipeline.

    1. Загрузить M1
    2. Ресемпл → заданный TF
    3. Вызвать strategy_detect_fn(bars) → list[Signal]
    4. Dedup (заглушка — нужны существующие сигналы)
    5. Trend filter
    6. Вернуть list[Signal]
    """
    if source == "ch":
        bars = load_m1_from_ch(symbol, hours, ch_host, ch_db)
    else:
        raise ValueError(f"Unknown source: {source}")

    tf_bars = resample_bars(bars, tf_minutes)

    if not tf_bars:
        return []

    signals = strategy_detect_fn(tf_bars)

    # Dedup пока без существующих (можно передать через closure)
    # Trend filter
    if len(tf_bars) >= 50:
        closes = [b["close"] for b in tf_bars[-100:]]
        trend = _sma_trend(closes)
        # Не блокируем на первом run — profitable unknown
        # Вызывающий решит

    return signals
