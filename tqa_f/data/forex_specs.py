"""Спеки форекс-инструментов и конверсия PnL в USD.

Источник: https://alfaforex.ru/upload/trading-terms/specifications-POINT.pdf (19.11.2026),
счёт POINT. Все величины спреда и форвардных пунктов в PDF даны в **пунктах**:
  5 знаков в цене → 1 пункт = 0.0001
  3 знака (JPY)    → 1 пункт = 0.01
  остальные (XAU)  → 1 пункт = минимальное изменение цены = 0.01

Контракт полей:
  contract  — размер контракта в базовой валюте (1 лот)
  point     — размер пункта в цене
  quote     — валюта котировки (в ней считается PnL)
  spread    — спред в пунктах (круг: половина на вход, половина на выход)
  commission— комиссия за ногу, USD на лот (у AlfaForex = 0)
  swap_l/s  — форвардные пункты за ночь: long / short (плюс = получаем)
  margin    — маржа, доля нотионала (плечо 1:40 = 2.5%)
"""

USD = 'USD'

# По POINT PDF: спреды валют, commission=0, свопы в пунктах.
# По RUB PDF: leverage 1:40 (margin 2.5%) для валютных пар,
#             XAU/USD: 1:19 (margin ≈5.26%) kval, 1:16 (≈6.25%) nekval.
# Пользователь требует 1:40 для всего форекса — gold_margin ниже можно переопределить.
# ТекущаяSetup: margin 0.025 (1:40) для всех, gold_margin отдельное поле.

MAJORS_MARGIN = 0.025    # 1:40 — валютные пары по POINT PDF
GOLD_MARGIN = 0.0526     # 1:19 — из RUB PDF dla XAU/USD (kval), может быть переопределено пользователем

SPECS = {
    'eurusd': dict(contract=100_000, point=0.0001, quote='USD', spread=1.4, commission=0.0,
                   swap_l=-0.70, swap_s=0.00, margin=MAJORS_MARGIN),
    'gbpusd': dict(contract=100_000, point=0.0001, quote='USD', spread=2.1, commission=0.0,
                   swap_l=-0.55, swap_s=-0.25, margin=MAJORS_MARGIN),
    'audusd': dict(contract=100_000, point=0.0001, quote='USD', spread=2.3, commission=0.0,
                   swap_l=-0.25, swap_s=-0.24, margin=MAJORS_MARGIN),
    'nzdusd': dict(contract=100_000, point=0.0001, quote='USD', spread=2.5, commission=0.0,
                   swap_l=-0.25, swap_s=-0.28, margin=MAJORS_MARGIN),
    'xauusd': dict(contract=100,     point=0.01,   quote='USD', spread=45.0, commission=0.0,
                   swap_l=-55.0, swap_s=0.00, margin=GOLD_MARGIN),
    'usdjpy': dict(contract=100_000, point=0.01,   quote='JPY', spread=2.0, commission=0.0,
                   swap_l=-0.25, swap_s=-1.50, margin=MAJORS_MARGIN),
    'usdcad': dict(contract=100_000, point=0.0001, quote='CAD', spread=2.1, commission=0.0,
                   swap_l=-0.30, swap_s=-0.70, margin=MAJORS_MARGIN),
    'usdchf': dict(contract=100_000, point=0.0001, quote='CHF', spread=2.0, commission=0.0,
                   swap_l=-0.10, swap_s=-0.50, margin=MAJORS_MARGIN),
    'eurjpy': dict(contract=100_000, point=0.01,   quote='JPY', spread=2.2, commission=0.0,
                   swap_l=-0.49, swap_s=-1.20, margin=MAJORS_MARGIN),
    'gbpjpy': dict(contract=100_000, point=0.01,   quote='JPY', spread=4.6, commission=0.0,
                   swap_l=-0.25, swap_s=-2.00, margin=MAJORS_MARGIN),
    'audjpy': dict(contract=100_000, point=0.01,   quote='JPY', spread=2.4, commission=0.0,
                   swap_l=-0.23, swap_s=-1.50, margin=MAJORS_MARGIN),
    'euraud': dict(contract=100_000, point=0.0001, quote='AUD', spread=4.0, commission=0.0,
                   swap_l=-1.00, swap_s=0.10, margin=MAJORS_MARGIN),
    'eurgbp': dict(contract=100_000, point=0.0001, quote='GBP', spread=2.0, commission=0.0,
                   swap_l=-0.47, swap_s=-0.07, margin=MAJORS_MARGIN),
}

# валюта котировки -> (символ CH для курса к USD, инвертировать)
QUOTE_RATE = {
    'JPY': ('usdjpy', True), 'CAD': ('usdcad', True), 'CHF': ('usdchf', True),
    'AUD': ('audusd', False), 'GBP': ('gbpusd', False), 'EUR': ('eurusd', False),
}


def spec(ticker: str) -> dict:
    s = SPECS.get(ticker)
    if s is None:
        raise KeyError(f'нет спека для {ticker}')
    # ms/sp в терминах движка: ms = пункт, sp = стоимость пункта на лот в валюте котировки
    return dict(s, ms=s['point'], sp=s['contract'] * s['point'])


def rate_symbols() -> list[str]:
    """Символы, нужные для конверсии PnL в USD."""
    need = {QUOTE_RATE[s['quote']][0] for s in SPECS.values() if s['quote'] != USD}
    return sorted(need)


def to_usd(quote: str, rate: float) -> float:
    """Множитель quote -> USD. rate = цена валютной пары из QUOTE_RATE."""
    if quote == USD:
        return 1.0
    _sym, invert = QUOTE_RATE[quote]
    return (1.0 / rate) if invert else rate
