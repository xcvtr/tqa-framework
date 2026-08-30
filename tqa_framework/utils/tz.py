"""ЕДИНЫЙ TZ-ХЕЛПЕР (СТАНДАРТ: ВСЁ В МСК, биржевое время MOEX).

Причина существования: TZ-баг вылезал десятки раз. Данные в CH/PG хранятся
в МСК (naive, без суффикса). Хост может быть в +08 (IRK) или UTC — НЕЛЬЗЯ
полагаться на локальную TZ хоста (datetime.fromtimestamp / datetime.now()
дают время хоста, а не МСК!).

ПРАВИЛА:
1. ЧИТАЕМ из БД: naive datetime = МСК (не конвертируем!).
2. ПИШЕМ в БД: naive datetime = МСК.
3. now() для логики — ТОЛЬКО через now_msk().
4. unix ↔ МСК — ТОЛЬКО через to_unix / from_unix.
5. ЗАПРЕЩЕНО: datetime.now(), datetime.fromtimestamp(ts) без tz=MSK,
   toUnixTimestamp(toDateTime(bt)) без 'Europe/Moscow' — они дают TZ хоста.

Примеры:
    from tqa_framework.utils.tz import now_msk, from_unix, to_unix, MSK
    now = now_msk()                          # naive МСК
    dt = from_unix(ts)                       # naive МСК из unix
    ts = to_unix(dt)                         # unix из naive МСК
"""
from __future__ import annotations

import datetime as _dt

MSK = _dt.timezone(_dt.timedelta(hours=3), name="MSK")

# Константы для SQL (ClickHouse)
# CH: toDateTime(bt, 'Europe/Moscow') — интерпретировать naive строку как МСК
# (без этого CH server TZ=UTC трактует строку как UTC → сдвиг −3ч!)
CH_TZ = "Europe/Moscow"


def now_msk() -> _dt.datetime:
    """Текущее время в МСК, naive (без tzinfo)."""
    return _dt.datetime.now(MSK).replace(tzinfo=None)


def now_msk_aware() -> _dt.datetime:
    """Текущее время в МСК, aware (с tzinfo=MSK)."""
    return _dt.datetime.now(MSK)


def from_unix(ts: float | int) -> _dt.datetime:
    """unix-epoch → naive datetime в МСК (корректно на ЛЮБОМ хосте)."""
    return _dt.datetime.fromtimestamp(float(ts), tz=MSK).replace(tzinfo=None)


def to_unix(dt: _dt.datetime) -> int:
    """naive datetime (МСК) → unix-epoch."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return int(dt.timestamp())


def from_msk_str(s: str) -> _dt.datetime:
    """Строка 'YYYY-MM-DD HH:MM:SS' (МСК) → naive datetime."""
    return _dt.datetime.fromisoformat(str(s))


def ensure_naive_msk(dt: _dt.datetime) -> _dt.datetime:
    """Привести к naive МСК (срезать tzinfo если есть, БЕЗ конверсии)."""
    return dt.replace(tzinfo=None)


def parse_moex_date(s: str) -> _dt.date:
    """'YYYY-MM-DD' → date (для торговых дней MOEX)."""
    return _dt.date.fromisoformat(str(s)[:10])


def msk_hour(dt: _dt.datetime) -> int:
    """Час в МСК (0-23) для любого datetime (naive считается МСК)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(MSK)
    return dt.hour


def is_trading_day(d: _dt.date) -> bool:
    """Будний день (MOEX: пн-пт). Суббота/воскресенье — нет."""
    return d.weekday() < 5
