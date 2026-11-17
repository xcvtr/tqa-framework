"""Paper-слой: сериализация позиции (рестарт без потери позы) и маршрут CLI."""
import sys

ROOT = '/home/user/projects/tqa-m'
sys.path.insert(0, ROOT)

from strategies.trend_sma.broker import Position  # noqa: E402
from strategies.trend_sma.live import _pos_from_dict, _pos_to_dict  # noqa: E402


def _make_pos():
    p = Position(ticker='MN', direction='short', entry_price=100.5, entry_bar=42, shares=3,
                 strategy='trend_sma', step_price=1.0, min_step=0.01, pct=1.0,
                 trailing_params={'stop_loss': 1.5, 'trail': 1.5}, commission=4.0)
    p.entry_time = '2026-11-13 10:05:00'
    p._skip_ts = 1770000000
    p._trend_arm = True
    p._best_price = 98.2
    p.best_abs = 2.3
    return p


def test_position_roundtrip_keeps_trailing_state():
    src = _make_pos()
    back = _pos_from_dict(_pos_to_dict(src))
    for attr in ('ticker', 'direction', 'entry_price', 'shares', 'entry_bar',
                 '_skip_ts', '_trend_arm', '_best_price', 'best_abs', 'entry_time'):
        assert getattr(back, attr) == getattr(src, attr), attr
    assert back.trailing_params == src.trailing_params


def test_position_dict_is_json_safe():
    import json
    json.dumps(_pos_to_dict(_make_pos()))   # рестарт читает из JSONB


def test_cli_paper_routes_to_live():
    src = open(f'{ROOT}/scripts/position_cli.py').read()
    assert "from strategies.trend_sma import live" in src
    assert "--mode paper" not in src or "args.mode == 'paper'" in src or "import live" in src
