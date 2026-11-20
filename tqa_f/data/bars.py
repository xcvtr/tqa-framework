"""Data layer: чистые функции над барами (CH-сырьё → дневные/5м ряды).

Здесь НЕТ импортов executor/position — этот модуль доступен образу детектора.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta as _td

import numpy as np

_TF_MIN = 5  # detect_minutes канона (tf=5)


def msk_date(ts) -> str | None:
    try:
        if hasattr(ts, 'astimezone'):
            return ts.astimezone(timezone(_td(hours=3))).date().isoformat()
        if isinstance(ts, str):
            return ts[:10]
    except Exception:
        pass
    return None


def group_days(ts_list) -> dict[str, tuple[int, int]]:
    """date_str -> (i0, i1) для отсортированного по времени списка."""
    out: dict[str, tuple[int, int]] = {}
    i0, cur = 0, None
    for i, t in enumerate(ts_list):
        dk = msk_date(t)
        if dk != cur:
            if cur is not None:
                out[cur] = (i0, i)
            cur, i0 = dk, i
    if cur is not None:
        out[cur] = (i0, len(ts_list))
    return out


def resample5(bars):
    """M1 → 5м как канон resample_bars: бакет ts.floor(5m), hi=max, lo=min, prc=последний."""
    out_ts, out_o, out_h, out_l, out_p, out_v = [], [], [], [], [], []
    cur = None
    for b in bars:
        t = b['ts']
        m5 = (t.hour * 60 + t.minute) // _TF_MIN
        key = (t.year, t.month, t.day, m5)
        if key != cur:
            m5m = m5 * _TF_MIN
            out_ts.append(t.replace(hour=m5m // 60, minute=m5m % 60, second=0))
            out_o.append(b['opn']); out_h.append(b['hi']); out_l.append(b['lo'])
            out_p.append(b['prc']); out_v.append(b.get('vol', 0.0))
            cur = key
        else:
            if b['hi'] > out_h[-1]: out_h[-1] = b['hi']
            if b['lo'] < out_l[-1]: out_l[-1] = b['lo']
            out_p[-1] = b['prc']
    return (out_ts, np.array(out_o, dtype='float64'), np.array(out_h, dtype='float64'),
            np.array(out_l, dtype='float64'), np.array(out_p, dtype='float64'),
            np.array(out_v, dtype='float64'))
