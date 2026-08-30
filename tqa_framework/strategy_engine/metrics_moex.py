"""MOEX strategy metrics — регистрирует detect-логику стратегий как метрики.

Каждая метрика возвращает float:
  > 0   → LONG сигнал (чем больше, тем увереннее)
  < 0   → SHORT сигнал
  0     → нет сигнала

Используется в YAML:
  mean_rev_signal > 0.1  → LONG
  mean_rev_signal < -0.1 → SHORT
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone, timedelta

from tqa_framework.strategy_engine.metrics import register_metric


# ─── Mean Reversion ───────────────────────────────────────────────

def _mean_rev_signal(bars: list[dict], params: dict, state: dict) -> float:
    """Mean reversion: retrace_pct > thr → SHORT (-1), < -thr → LONG (+1).
    params: ret_thr=0.004, trend_filter=true
    """
    rt = float(params.get("ret_thr", 0.004))
    trend = params.get("trend_filter", "sma20") == "sma20"

    if len(bars) < 3:
        return 0.0

    last = bars[-1]
    close = float(last["close"])
    open_ = float(last["open"])
    if open_ <= 0 or close <= 0:
        return 0.0

    ret = (close - open_) / open_

    if abs(ret) <= rt:
        return 0.0

    direction = -1.0 if ret > rt else 1.0  # SHORT if ret up, LONG if ret down

    # Trend filter SMA20
    if trend and len(bars) >= 21:
        closes = [float(b["close"]) for b in bars[-21:-1] if float(b["close"]) > 0]
        if len(closes) >= 20:
            sma20 = sum(closes) / len(closes)
            if direction == -1.0 and close > sma20 * 1.005:
                return 0.0  # strong uptrend — no short
            if direction == 1.0 and close < sma20 / 1.005:
                return 0.0  # strong downtrend — no long

    return direction


# ─── Stop Hunt ────────────────────────────────────────────────────

def _stop_hunt_signal(bars: list[dict], params: dict, state: dict) -> float:
    """LONG: пробой вниз диапазона + откат. SHORT: пробой вверх + откат.
    params: lookback=40, retrace_min=0.1
    Returns: +score (LONG), -score (SHORT), 0 (none)
    """
    if len(bars) < 30:
        return 0.0

    lookback = int(params.get("lookback", 40))
    ret_min = float(params.get("retrace_min", 0.1))

    if len(bars) < lookback + 1:
        return 0.0

    low_hist = [float(b["low"]) for b in bars[-lookback:-1]]
    high_hist = [float(b["high"]) for b in bars[-lookback:-1]]
    if not low_hist or not high_hist:
        return 0.0

    cur = bars[-1]
    lo = float(cur["low"])
    hi = float(cur["high"])
    close = float(cur["close"])
    if lo <= 0 or hi <= 0 or close <= 0:
        return 0.0

    min_lo = min(low_hist)
    max_hi = max(high_hist)

    # LONG: пробой вниз + откат
    if lo < min_lo and close > lo + ret_min * (hi - lo):
        score = (min_lo - lo) / (hi - lo + 0.001)
        return min(float(score), 2.0)

    # SHORT: пробой вверх + откат
    if hi > max_hi and close < hi - ret_min * (hi - lo):
        score = (hi - max_hi) / (hi - lo + 0.001)
        return max(-float(score), -2.0)

    return 0.0


# ─── Impulse Return ───────────────────────────────────────────────

def _impulse_return_signal(bars: list[dict], params: dict, state: dict) -> float:
    """Импульс + откат по Фибо. params: imp_bars=12, imp_pct=0.5, retrace=0.618, min_vol_pct=0.8"""
    if len(bars) < 30:
        return 0.0

    imp_bars = int(params.get("imp_bars", 12))
    imp_pct = float(params.get("imp_pct", 0.5))
    retrace = float(params.get("retrace", 0.618))
    min_vol_pct = float(params.get("min_vol_pct", 0.8))

    if len(bars) < imp_bars + 2:
        return 0.0

    cur = bars[-1]
    close = float(cur["close"])
    hi = float(cur["high"])
    lo = float(cur["low"])
    vol = float(cur.get("volume", 0))
    if close <= 0 or hi <= 0 or lo <= 0 or vol <= 0:
        return 0.0

    recent = bars[-(imp_bars + 1):]
    start_prc = float(recent[0]["close"])
    all_hi = [float(b["high"]) for b in recent]
    all_lo = [float(b["low"]) for b in recent]
    all_vol = [float(b.get("volume", 0)) for b in recent]
    max_prc = max(all_hi)
    min_prc = min(all_lo)
    if start_prc <= 0:
        return 0.0

    # Volume filter
    avg_vol = statistics.mean(all_vol) if all_vol else 0
    vol_hist = [float(b.get("volume", 0)) for b in bars[:-1] if float(b.get("volume", 0)) > 0]
    median_vol = statistics.median(vol_hist) if len(vol_hist) > 10 else avg_vol
    if median_vol <= 0:
        median_vol = avg_vol
    if avg_vol < median_vol * min_vol_pct:
        return 0.0

    # LONG: бычий импульс → откат вниз
    up_move = (max_prc - start_prc) / start_prc * 100
    if up_move >= imp_pct:
        impulse_range = max_prc - start_prc
        if impulse_range > 0:
            curr_ret = (max_prc - close) / impulse_range
            if curr_ret >= retrace:
                return min(float(curr_ret * 2), 5.0)

    # SHORT: медвежий импульс → откат вверх
    down_move = (start_prc - min_prc) / start_prc * 100
    if down_move >= imp_pct:
        impulse_range = start_prc - min_prc
        if impulse_range > 0:
            curr_ret = (close - min_prc) / impulse_range
            if curr_ret >= retrace:
                return max(-float(curr_ret * 2), -5.0)

    return 0.0


# ─── Day of Week ──────────────────────────────────────────────────

def _dayofweek_signal(bars: list[dict], params: dict, state: dict) -> float:
    """LONG в Пн если prev_week > 0, SHORT в Чт если prev_week < 0.
    params: skip_july=true
    """
    if not bars:
        return 0.0

    ts = bars[-1].get("ts")
    if not ts:
        return 0.0

    # Handle both unixtime and ISO string timestamps
    if isinstance(ts, (int, float)):
        now_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(ts, str):
        now_utc = datetime.fromisoformat(str(ts).replace("Z", "+00:00") if "Z" in str(ts) else str(ts))
    else:
        now_utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    skip_july = params.get("skip_july", "true") in ("true", "True", True)

    if skip_july and now_utc.month == 7:
        return 0.0

    dow = now_utc.weekday()  # 0=Mon

    # Prev week return
    daily = {}
    for b in bars:
        bt = b.get("ts")
        if isinstance(bt, (int, float)):
            d = datetime.fromtimestamp(bt, tz=timezone.utc).date()
        elif isinstance(bt, str):
            d = datetime.fromisoformat(bt.replace("Z", "+00:00") if "Z" in bt else bt).date()
        else:
            continue
        daily[d] = b["close"]

    cur_date = now_utc.date()
    dates = sorted(d for d in daily if d < cur_date)
    if len(dates) < 2:
        return 0.0

    mon = cur_date - timedelta(days=cur_date.weekday())
    prev_mon = mon - timedelta(days=7)
    prev_sun = prev_mon + timedelta(days=6)
    idx = [i for i, d in enumerate(dates) if prev_mon <= d <= prev_sun]
    if len(idx) < 2:
        return 0.0

    c0 = daily[dates[idx[0]]]
    c1 = daily[dates[idx[-1]]]
    if c0 <= 0:
        return 0.0
    prev_ret = c1 / c0 - 1.0

    if dow == 0 and prev_ret > 0:  # Monday
        return 1.0
    if dow == 3 and prev_ret < 0:  # Thursday
        return -1.0
    return 0.0


# ─── Dragon ───────────────────────────────────────────────────────

def _dragon_signal(bars: list[dict], params: dict, state: dict) -> float:
    """Dragon pattern: SHORT (+1) or LONG (-1).
    params: impulse_pct=0.3, retrace_max_pct=70, hump_extension=0.1, lookback=100
    """
    if len(bars) < 30:
        return 0.0

    imp_pct = float(params.get("impulse_pct", 0.3))
    ret_max = float(params.get("retrace_max_pct", 70))
    hump_ext = float(params.get("hump_extension", 0.1))
    lookback = int(params.get("lookback", 100))

    n = len(bars)
    max_start = max(0, n - lookback)

    # SHORT dragon: impulse UP → retrace → hump above neck → close below retrace_low
    for neck_len in range(3, 12):
        neck_start = n - neck_len - 10
        if neck_start < max_start:
            continue
        impulse_bars = bars[neck_start:n - 10]
        if len(impulse_bars) < 3:
            continue
        pre = float(impulse_bars[0]["close"])
        imp_hi = max(float(b["high"]) for b in impulse_bars)
        if pre <= 0:
            continue
        imp_up_pct = (imp_hi - pre) / pre * 100
        if imp_up_pct < imp_pct:
            continue
        # Find neck peak
        neck_peak = imp_hi
        neck_peak_idx = neck_start + max(range(len(impulse_bars)), key=lambda i: float(impulse_bars[i]["high"]))
        # Retrace: find low after peak
        retrace_low = neck_peak
        for j in range(neck_peak_idx + 1, min(neck_peak_idx + 6, n)):
            lo = float(bars[j]["low"])
            if lo < retrace_low:
                retrace_low = lo
        impulse_range = neck_peak - pre
        if impulse_range <= 0:
            continue
        if (neck_peak - retrace_low) / impulse_range > ret_max / 100:
            continue
        # Humps: false breakout above neck
        hump_found = False
        for j in range(neck_peak_idx + 6, n):
            hi = float(bars[j]["high"])
            if hi > neck_peak and (hi - neck_peak) / neck_peak * 100 >= hump_ext:
                hump_found = True
                break
        if not hump_found:
            continue
        # Tail: close below retrace_low
        close = float(bars[-1]["close"])
        if close < retrace_low:
            return -1.0  # SHORT

    # LONG dragon: impulse DOWN → retrace → hump below neck → close above retrace_hi
    for neck_len in range(3, 12):
        neck_start = n - neck_len - 10
        if neck_start < max_start:
            continue
        impulse_bars = bars[neck_start:n - 10]
        if len(impulse_bars) < 3:
            continue
        pre = float(impulse_bars[0]["close"])
        imp_lo = min(float(b["low"]) for b in impulse_bars)
        if pre <= 0:
            continue
        imp_dn_pct = (pre - imp_lo) / pre * 100
        if imp_dn_pct < imp_pct:
            continue
        neck_low = imp_lo
        neck_low_idx = neck_start + min(range(len(impulse_bars)), key=lambda i: float(impulse_bars[i]["low"]))
        retrace_high = neck_low
        for j in range(neck_low_idx + 1, min(neck_low_idx + 6, n)):
            hi = float(bars[j]["high"])
            if hi > retrace_high:
                retrace_high = hi
        impulse_range = pre - neck_low
        if impulse_range <= 0:
            continue
        if (retrace_high - neck_low) / impulse_range > ret_max / 100:
            continue
        hump_found = False
        for j in range(neck_low_idx + 6, n):
            lo_h = float(bars[j]["low"])
            if lo_h < neck_low and (neck_low - lo_h) / neck_low * 100 >= hump_ext:
                hump_found = True
                break
        if not hump_found:
            continue
        close = float(bars[-1]["close"])
        if close > retrace_high:
            return 1.0  # LONG

    return 0.0


# ─── Lunch Reversal ──────────────────────────────────────────────

def _lunch_rev_signal(bars: list[dict], params: dict, state: dict) -> float:
    """Lunch reversal at 13:00 MSK.
    SHORT если цена выросла с 10:00 до 13:00. LONG если упала.
    params: min_move_pct=0.1
    """
    if len(bars) < 2:
        return 0.0

    ts = bars[-1].get("ts")
    if not ts:
        return 0.0

    min_move = float(params.get("min_move_pct", 0.1))

    # Check if current bar is 13:00 MSK
    def _msk_hour(ts):
        if isinstance(ts, (int, float)):
            return (datetime.utcfromtimestamp(ts).hour + 3) % 24
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt.hour + 3) % 24

    if _msk_hour(ts) != 13:
        return 0.0
    if isinstance(ts, (int, float)):
        minute = datetime.utcfromtimestamp(ts).minute
    else:
        minute = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).minute
    if minute != 0:
        return 0.0

    price_10 = None
    for bar in reversed(bars):
        bt = bar.get("ts")
        if not bt:
            continue
        if isinstance(bt, str):
            bdt = datetime.fromisoformat(bt.replace("Z", "+00:00"))
        else:
            bdt = datetime.utcfromtimestamp(bt)
        if _msk_hour(bt) == 10 and bdt.minute == 0:
            price_10 = float(bar["close"])
            break
    if price_10 is None:
        return 0.0

    close = float(bars[-1]["close"])
    change = (close - price_10) / price_10 * 100

    if change > min_move:
        return -1.0  # SHORT
    if change < -min_move:
        return 1.0   # LONG
    return 0.0


# ─── Register all MOEX metrics ────────────────────────────────────

_MOEX_METRICS = [
    ("mean_rev_signal", _mean_rev_signal),
    ("stop_hunt_signal", _stop_hunt_signal),
    ("impulse_return_signal", _impulse_return_signal),
    ("dayofweek_signal", _dayofweek_signal),
    ("dragon_signal", _dragon_signal),
    ("lunch_rev_signal", _lunch_rev_signal),
]

for name, fn in _MOEX_METRICS:
    try:
        register_metric(name, fn)
    except ValueError:
        pass  # already registered