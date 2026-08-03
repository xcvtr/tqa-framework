"""Risk management — common pool, sizing, trend filter.

risk_mult = max(0.2, min(1.2, 24000.0 / max(eq, 500)))
MTM DD: -18% (×1.0), -22% (×1.2 boost)
"""

from __future__ import annotations


def calc_risk_mult(equity: float) -> float:
    """Динамический risk multiplier."""
    return max(0.2, min(1.2, 24000.0 / max(equity, 500)))


def calc_contracts(equity: float, risk_pct: float,
                   price: float, go: float) -> float:
    """Количество контрактов: min(по риску, по ГО)."""
    ct_risk = equity * risk_pct / 100 / price
    ct_go = equity / go if go > 0 else ct_risk
    return min(ct_risk, ct_go)


def calc_lot_forex(equity: float, risk_pct: float, sl_pips: float,
                   pip_value: float = 10.0, min_lot: float = 0.01) -> float:
    """Лот для forex: риск $ / (SL в пипсах × стоимость пипса).

    risk_pct — доля капитала на сделку (0.01 = 1%).
    sl_pips — стоп в пипсах (pip = 10 × point MT5).
    pip_value — $ за 1 пипс на 1 лот (AlfaForex: 10.0).
    """
    sl_pips = max(sl_pips, 10.0)
    lot = equity * risk_pct / (sl_pips * pip_value)
    return max(min_lot, round(lot, 2))


def trend_filter(trend: str, direction: str, profitable: bool) -> bool:
    """Trend filter: блокирует контр-тренд после прибыльной сделки."""
    if not profitable:
        return True
    if trend == "uptrend" and direction == "SHORT":
        return False
    if trend == "downtrend" and direction == "LONG":
        return False
    return True


def sma_50_trend(prices: list[float]) -> str:
    """Определить тренд по SMA 50."""
    if len(prices) < 50:
        return "neutral"
    sma = sum(prices[-50:]) / 50
    current = prices[-1]
    if current > sma * 1.005:
        return "uptrend"
    elif current < sma * 0.995:
        return "downtrend"
    return "neutral"
