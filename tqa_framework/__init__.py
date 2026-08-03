"""tqa_framework — единый trading framework: crypto, forex, MOEX.

Установка (editable):
    pip install -e /home/user/projects/tqa-framework

Использование в проекте-стратегии:
    from tqa_framework.engine.exchange_base import Signal, Position
    from tqa_framework.engine.backtester import Backtester
    from tqa_framework.engine.detect import load_m1_from_ch, resample_bars
    from tqa_framework.engine.risk import calc_risk_mult, calc_lot_forex
"""

__version__ = "0.3.0"
