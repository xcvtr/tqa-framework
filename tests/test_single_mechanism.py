"""Контракт «один механизм»: бэктест и paper/live считают одной функцией run_days."""

ROOT = '/home/user/projects/tqa-m'


def _src(mod_path: str) -> str:
    with open(f'{ROOT}/{mod_path}') as f:
        return f.read()


def test_engine_is_single_source_of_decisions():
    engine = _src('strategies/trend_sma/engine.py')
    # Решения живут только в движке
    for needle in ('detect(sig_bars', 'size_position(', 'check_portfolio_gates(', '_update_trend_sma('):
        assert needle in engine, f'в engine.py нет {needle}'


def test_drivers_do_not_duplicate_logic():
    """Драйверы (история/папер) не содержат detect/сайзинга/трейлинга — только run_days."""
    for path in ('strategies/trend_sma/self_tester.py', 'strategies/trend_sma/live.py'):
        src = _src(path)
        assert 'run_days(' in src, f'{path} не вызывает run_days'
        for bad in ('detect(', 'size_position(', 'check_portfolio_gates(', '_update_trend_sma('):
            assert bad not in src, f'{path} содержит дубль логики: {bad}'


def test_live_replay_uses_engine_module():
    src = _src('strategies/trend_sma/live.py')
    assert 'from strategies.trend_sma.engine import' in src
    assert 'run_days' in src
