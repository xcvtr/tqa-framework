"""
Спека AlfaForex из официальных PDF (POINT + marginal-RUB, 20.11.2026).
- POINT: https://alfaforex.ru/upload/trading-terms/specifications-POINT.pdf
- RUB:   https://alfaforex.ru/upload/trading-terms/marginal-RUB.pdf

Кортеж SPECS:
  (contract, point, pip, spread, commission, swap_long, swap_short, margin, quote)

point = минимальная единица цены (0.0001, 0.01 и т.д.)
pip = стоимость пункта в валюте котировки (== point для валют, == point для metals)
sp = contract * point (стоимость 1 пункта на весь лот)
"""

from __future__ import annotations

USD = "USD"
MAJORS_MARGIN = 0.025    # 1:40
GOLD_MARGIN = 0.0526     # 1:19 (kval investor)

# (contract, point, pip, spread, commission, swap_long, swap_short, margin, quote)
SPECS = {
    # Majors
    "eurusd":  (100000, 0.0001, 0.0001, 1.4,  0, -0.70,  0.00, MAJORS_MARGIN, "EUR"),
    "gbpusd":  (100000, 0.0001, 0.0001, 2.1,  0, -0.55, -0.25, MAJORS_MARGIN, "GBP"),
    "usdjpy":  (100000, 0.01,   0.01,   2.0,  0, -0.25, -1.50, MAJORS_MARGIN, "JPY"),
    "usdchf":  (100000, 0.0001, 0.0001, 2.0,  0, -0.10, -0.50, MAJORS_MARGIN, "CHF"),
    "usdcad":  (100000, 0.0001, 0.0001, 2.1,  0, -0.30, -0.70, MAJORS_MARGIN, "CAD"),
    "audusd":  (100000, 0.0001, 0.0001, 2.3,  0, -0.25, -0.24, MAJORS_MARGIN, "AUD"),
    "nzdusd":  (100000, 0.0001, 0.0001, 2.5,  0, -0.25, -0.28, MAJORS_MARGIN, "NZD"),
    # Crosses
    "eurjpy":  (100000, 0.01,   0.01,   2.2,  0, -0.49, -1.20, MAJORS_MARGIN, "EUR"),
    "gbpjpy":  (100000, 0.01,   0.01,   4.6,  0, -0.25, -2.00, MAJORS_MARGIN, "GBP"),
    "eurgbp":  (100000, 0.0001, 0.0001, 2.0,  0, -0.47, -0.07, MAJORS_MARGIN, "EUR"),
    "eurchf":  (100000, 0.0001, 0.0001, 2.1,  0, -0.20, -0.54, MAJORS_MARGIN, "EUR"),
    "euraud":  (100000, 0.0001, 0.0001, 4.0,  0, -1.00,  0.10, MAJORS_MARGIN, "EUR"),
    "eurcad":  (100000, 0.0001, 0.0001, 4.5,  0, -0.83,  0.10, MAJORS_MARGIN, "EUR"),
    "eurnzd":  (100000, 0.0001, 0.0001, 6.1,  0, -1.05,  0.10, MAJORS_MARGIN, "EUR"),
    "eurnok":  (100000, 0.0001, 0.0001, 45.0, 0, -10.00,  0.00, MAJORS_MARGIN, "EUR"),
    "audjpy":  (100000, 0.01,   0.01,   2.4,  0, -0.23, -1.50, MAJORS_MARGIN, "AUD"),
    "audcad":  (100000, 0.0001, 0.0001, 2.2,  0, -0.35, -0.30, MAJORS_MARGIN, "AUD"),
    "audnzd":  (100000, 0.0001, 0.0001, 5.4,  0, -0.50, -0.27, MAJORS_MARGIN, "AUD"),
    "audchf":  (100000, 0.0001, 0.0001, 2.6,  0,  0.05, -0.73, MAJORS_MARGIN, "AUD"),
    "gbpaud":  (100000, 0.0001, 0.0001, 5.4,  0, -0.79, -0.40, MAJORS_MARGIN, "GBP"),
    "gbpcad":  (100000, 0.0001, 0.0001, 4.5,  0, -0.58, -0.40, MAJORS_MARGIN, "GBP"),
    "gbpchf":  (100000, 0.0001, 0.0001, 4.0,  0,  0.01, -0.75, MAJORS_MARGIN, "GBP"),
    "gbpnzd":  (100000, 0.0001, 0.0001, 6.8,  0, -0.75, -0.46, MAJORS_MARGIN, "GBP"),
    "chfjpy":  (100000, 0.01,   0.01,   2.5,  0, -0.30, -1.20, MAJORS_MARGIN, "CHF"),
    "usdnok":  (100000, 0.0001, 0.0001, 43.0, 0, -3.50, -3.50, MAJORS_MARGIN, "NOK"),
    "usdmxn":  (100000, 0.0001, 0.0001, 75.0, 0, -50.00, 12.10, 0.050, "MXN"),
    "usdzar":  (100000, 0.0001, 0.0001, 85.0, 0, -28.50,  8.00, 0.050, "ZAR"),
    "usdcny":  (100000, 0.0001, 0.0001, 20.0, 0, -4.00,  -5.00, MAJORS_MARGIN, "CNY"),
    "eurcny":  (100000, 0.0001, 0.0001, 25.0, 0, -5.00,  -3.00, MAJORS_MARGIN, "EUR"),
    "usdsgd":  (100000, 0.0001, 0.0001, 5.0,  0, -0.10, -0.33, MAJORS_MARGIN, "SGD"),
    "usdils":  (100000, 0.0001, 0.0001, 7.0,  0, -3.00, -4.50, 0.050, "ILS"),
    "usdsek":  (100000, 0.0001, 0.0001, 43.0, 0,  0.00, -7.00, MAJORS_MARGIN, "SEK"),
    "eursek":  (100000, 0.0001, 0.0001, 45.0, 0, -3.00, -5.00, MAJORS_MARGIN, "EUR"),
    "usddkk":  (100000, 0.0001, 0.0001, 20.0, 0, -0.50, -3.00, 0.100, "DKK"),
    "eurdkk":  (100000, 0.0001, 0.0001, 20.0, 0, -1.00, -1.00, 0.100, "EUR"),
    "usdtry":  (100000, 0.0001, 0.0001, 3000.0, 0, -385.00, 35.00, 0.100, "TRY"),
    "eurtry":  (100000, 0.0001, 0.0001, 3200.0, 0, -375.00, 25.00, 0.100, "EUR"),
    # RUB pairs (quote = RUB)
    "usdrub":  (100000, 0.0001, 0.0001, 15.0, 0, -500.00, 10.00, 0.030, "RUB"),
    "eurrub":  (100000, 0.0001, 0.0001, 20.0, 0, -650.00, 15.00, 0.031, "RUB"),
    "cnyrub":  (100000, 0.0001, 0.0001, 1.4,  0, -110.00,  0.00, 0.029, "RUB"),
    "tryrub":  (100000, 0.0001, 0.0001, 25.0, 0, -45.00, -10.00, 0.100, "RUB"),
    # Metals
    "xauusd":  (100,  0.01, 0.01, 45.0, 0, -55.00,   0.00, GOLD_MARGIN, "USD"),
    "xagusd":  (5000, 0.01, 0.01, 90.0, 0, -55.00, -15.00, 0.100, "USD"),
    "xpdusd":  (100,  0.01, 0.01, 600.0,0, -30.00, -10.00, 0.100, "USD"),
    "xptusd":  (100,  0.01, 0.01, 500.0,0, -30.00, -10.00, 0.100, "USD"),
    # Crypto CFDH
    "btcusd":  (1, 1.0, 1.0, 0.0006, 0, -0.042, -0.042, 0.001, "USD"),
    "ethusd":  (1, 1.0, 1.0, 0.0009, 0, -0.042, -0.042, 0.001, "USD"),
}

# quote currency -> (symbol_pair, invert) для конверсии PnL в USD
QUOTE_RATE = {
    "JPY": ("usdjpy", True), "CAD": ("usdcad", True), "CHF": ("usdchf", True),
    "AUD": ("audusd", False), "GBP": ("gbpusd", False), "EUR": ("eurusd", False),
    "NOK": ("usdnok", True), "SEK": ("usdsek", True), "DKK": ("usddkk", True),
    "MXN": ("usdmxn", True), "ZAR": ("usdzar", True), "CNY": ("usdcny", True),
    "SGD": ("usdsgd", True), "ILS": ("usdils", True), "TRY": ("usdtry", True),
    "RUB": ("usdrub", True),
}

# ticker -> quote currency (строится из SPECS)
QUOTE_MAP = {k: v[8] for k, v in SPECS.items()}


def spec(ticker: str) -> dict:
    """Вернуть spec dict для ticker."""
    s = SPECS.get(ticker)
    if s is None:
        raise KeyError(f"нет спека для {ticker}")
    contract, point, pip, spread, comm, sw_l, sw_s, margin, quote = s
    return {
        "contract": contract, "point": point, "pip": pip,
        "spread": spread, "commission": comm,
        "swap_l": sw_l, "swap_s": sw_s, "margin": margin,
        "quote": quote,
        "ms": point,
        "sp": contract * point,
    }


def rate_symbols() -> list[str]:
    """Символы, нужные для конверсии PnL в USD."""
    need = set()
    for quote in QUOTE_MAP.values():
        if quote == USD:
            continue
        sym, _ = QUOTE_RATE.get(quote, ("usdrub", True))
        need.add(sym)
    return sorted(need)


def to_usd(quote: str, rate: float) -> float:
    """Множитель quote -> USD. rate = цена валютной пары из QUOTE_RATE."""
    if quote == USD:
        return 1.0
    _sym, invert = QUOTE_RATE.get(quote, ("usdrub", True))
    return (1.0 / rate) if invert else rate
