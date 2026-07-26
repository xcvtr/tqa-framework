"""Detect — общий framework для поиска сигналов.

Загрузка M1 из CH → ресемпл → detect strategy → dedup → trend filter.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

import requests

import json

from engine.exchange_base import Signal


_CH_URL = os.environ.get("CH_URL", "http://10.0.0.60:8123")


def load_m1_from_ch(
    symbol: str,
    hours: int,
    host: str = "",
    db: str = "",
    end_time: str = "",
) -> list[dict]:
    """Загрузить M1 бары из ClickHouse.

    Args:
        symbol: Тикер (GD, GZ, EURUSD и т.д.)
        hours: За сколько часов до end_time загрузить
        host: CH URL (из CH_URL env или по умолчанию)
        db: БД (moex, forex)
        end_time: Фиксированное окончание (ISO). Если пусто — now()

    Returns:
        list[dict] с ключами: ts, open, high, low, close, volume
    """
    url = host or _CH_URL
    if not db:
        raise ValueError("db обязателен: 'moex' или 'forex'")

    end_condition = f"'{end_time}'" if end_time else "now()"

    query = f"""
    SELECT
        bt as ts,
        opn as open,
        hi as high,
        lo as low,
        prc as close,
        vol as volume
    FROM {db}.bars
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
    return bars


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
        result.append({
            "ts": key,
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })

    return result


def dedup_signals(
    new: list[Signal],
    existing: list[Signal],
    existing_trades: list[dict],
) -> list[Signal]:
    """Dedup: исключить уже торгованные сигналы.

    Сравнение по (symbol + direction + price_bucket).
    price_bucket = round(price, 1) — для MOEX фьючерсов.
    """
    seen = set()
    for s in existing:
        seen.add((s.symbol, s.direction, round(s.price, 1)))
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
    from engine.risk import trend_filter as _tf
    return _tf(trend, direction, profitable)


def _sma_trend(prices: list[float]) -> str:
    """Определить тренд по SMA 50."""
    from engine.risk import sma_50_trend
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
