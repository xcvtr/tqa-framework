"""Контракт слоёв: решения — в position_manager, исполнение — в broker.

Тесты без сети и без БД: только чистые функции/классы.
"""
import sys

sys.path.insert(0, '/home/user/projects/tqa-f')

from strategies.trend_sma.broker import BrokerSim, Position
from strategies.trend_sma.position_manager import PositionManager, size_position, frac_size_position


def _pos(direction='long', entry=100.0, shares=10, params=None):
    return Position(ticker='MN', direction=direction, entry_price=entry, entry_bar=0,
                    shares=shares, strategy='trend_sma', step_price=1.0, min_step=1.0,
                    trailing_params=params or {'trail_mode': 'trend_sma', 'stop_loss': 1.5})


def test_broker_is_executor_only():
    """BrokerSim не принимает решений: только fill/slippage/ликвидность/PnL."""
    b = BrokerSim()
    assert b.check_liquidity(1, 100) == 1.0
    assert b.check_liquidity(30, 100) == 3.0
    assert b.check_liquidity(0, 0) == 2.0
    # по имени: тест самости детектора чистит sys.modules → второй экземпляр класса
    assert type(b.pm).__name__ == 'PositionManager', "решения должны жить в PositionManager"


def test_stop_loss_closes_via_position_manager():
    """SL 1.5%: решение в pm, исполнение в broker (_close_market)."""
    b = BrokerSim(commission=0)
    pos = _pos(entry=100.0, shares=1)
    b.update(pos, 1, hi=101.0, lo=98.0, prc=98.2)   # lo <= 98.5 → SL
    assert pos.closed and pos.exit_reason == 'stop_loss'
    assert pos.exit_price is not None and pos.exit_price < 98.5


def test_pyramid_increases_shares():
    """Пирамидинг меняет размер позы: +1 шаг при движении >= thr."""
    b = BrokerSim(commission=0)
    pos = _pos(entry=100.0, shares=10, params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                               'oi_pyramid_add': 1.0, 'oi_pyramid_thr': 0.015,
                                               'oi_pyramid_max': 6})
    b.update(pos, 1, hi=102.0, lo=99.9, prc=102.0)  # +2% ≥ 1.5% → 1 ступень
    assert pos.shares == 20, f"ожидали 10*(1+1.0*1)=20, получили {pos.shares}"


def test_size_position_formula():
    assert size_position(200000, 0.025, 8.0, 458) == min(int(200000 * 0.025 / 458),
                                                         int(200000 * 8.0 / 458))
    assert size_position(200000, 0.025, 8.0, 458) == 10


def test_frac_size_position_step():
    """Дробный сайзинг FX: лот кратен шагу, минимум один шаг."""
    assert frac_size_position(475, 0.025, 8.0, 10000, 0.01) == 0.01   # риск ниже шага → шаг
    assert frac_size_position(200000, 0.025, 8.0, 10000, 0.01) == 0.5  # 200000*0.025/10000 = 0.5


def test_pyramid_keeps_fractional_shares():
    """Дробный лот не должен схлопываться в 0 при пирамидинге."""
    b = BrokerSim(commission=0)
    pos = _pos(entry=100.0, shares=0.01, params={'trail_mode': 'trend_sma', 'stop_loss': 1.5,
                                                 'oi_pyramid_add': 1.0, 'oi_pyramid_thr': 0.015,
                                                 'oi_pyramid_max': 6})
    b.update(pos, 1, hi=102.0, lo=99.9, prc=102.0)
    assert abs(pos.shares - 0.02) < 1e-9, f"ожидали 0.02, получили {pos.shares}"


def test_fx_size_position_margin_cap():
    """FX: лот по риску на стопе, ограниченный маржой; золото 0.01 шаг."""
    risk_lot = 0.015 * 4400 * 100      # 6600 USD на 1.0 лот при стопе 1.5%
    margin_lot = 0.06 * 4400 * 100     # 26400 USD залога на 1.0 лот
    from strategies.trend_sma.position_manager import fx_size_position
    assert fx_size_position(475, 0.025, risk_lot, margin_lot, 0.01) == 0.01   # ниже минимума
    assert fx_size_position(2000, 0.025, risk_lot, margin_lot, 0.01) == 0.01  # 0.0076 → шаг
    assert fx_size_position(10_000, 0.025, risk_lot, margin_lot, 0.01) == 0.03
    # маржа жёстче риска: 30 000 строк при плече 16.7 → 1.13 лота, риск даёт 0.11
    assert fx_size_position(30_000, 0.5, risk_lot, margin_lot, 0.01) == 1.13


def test_er_series_gate():
    """ER: прямой тренд → 1.0, пила → ~0, первые n точек 0."""
    from strategies.trend_sma.engine import er_series
    trend = [100.0 + i for i in range(80)]
    saw = [100.0 + (2.0 if i % 2 else 0.0) for i in range(80)]
    et, es = er_series(trend), er_series(saw)
    assert et[:50] == [0.0] * 50 and et[50] == 1.0
    assert es[50] < 0.1
