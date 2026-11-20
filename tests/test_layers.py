"""Контракт слоёв: коннектор даёт филлы, детектор отделён от исполнителя."""
import sys

ROOT = '/home/user/projects/tqa-f'
sys.path.insert(0, ROOT)

from tqa_f.connector.exchange_base import ExchangeBase, ExchangeConfig, Signal  # noqa: E402
from tqa_f.connector.exchange_mock import ExchangeMock  # noqa: E402


def test_mock_implements_connector_contract():
    m = ExchangeMock(ExchangeConfig(name='mock'), balance=100.0)
    assert isinstance(m, ExchangeBase)
    m.set_price('MN', 100.0)
    assert m.get_price('MN') == 100.0
    sig = Signal(symbol='MN', direction='LONG', price=100.0, timestamp='2026-11-13')
    pos = m.open_position(sig, 3)
    assert pos and pos.quantity == 3
    assert len(m.get_positions()) == 1
    assert m.close_position(pos) is True
    assert m.get_positions() == []
    assert m.get_account_balance() == 100.0
    # филл-цена не искажается: импакт считает executor
    assert m.fill_price('MN', 'close', 99.5) == 99.5


def test_broker_uses_connector_when_given():
    from strategies.trend_sma.broker import BrokerSim
    b = BrokerSim(connector=ExchangeMock())
    assert b.connector is not None
    assert BrokerSim().connector is None


def test_engine_takes_signals_from_source_not_detect():
    src = open(f'{ROOT}/strategies/trend_sma/engine.py').read()
    assert 'cfg.signal_source' in src
    # при внешнем источнике detect не вызывается
    assert 'if cfg.signal_source is None:' in src


def test_pg_mode_never_imports_detector():
    """Исполнитель в режиме pg сигналов не должен даже импортировать detect."""
    import subprocess
    code = ("import sys;from strategies.trend_sma.engine import Config;"
            "Config(start='2026-01-01', signal_source=object(), connector=None);"
            "print('strategies.trend_sma.detect' in sys.modules)")
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                         capture_output=True, text=True,
                         env={'PYTHONPATH': ROOT, 'PATH': '/usr/bin:/bin'})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == 'False', out.stdout


def test_pg_signal_source_does_not_rely_on_processed_flag():
    src = open(f'{ROOT}/strategies/trend_sma/signals_pg.py').read()
    assert 'pending_signals' in src
    assert 'mark_processed' not in src   # идемпотентность по (symbol, дата)
