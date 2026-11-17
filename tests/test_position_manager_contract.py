"""Контракт слоёв: решения — в position_manager, исполнение — в broker.

Тесты без сети и без БД: только чистые функции/классы.
"""
import sys

sys.path.insert(0, '/home/user/projects/tqa-m')

from strategies.trend_sma.broker import BrokerSim, Position
from strategies.trend_sma.position_manager import PositionManager, size_position


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
