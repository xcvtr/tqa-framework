import os
import subprocess
import sys

ROOT = '/home/user/projects/tqa-m'


def _run(args):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          cwd=ROOT, env={**os.environ, 'PYTHONPATH': ROOT})


def test_detector_cli_check():
    r = _run(['scripts/detector_cli.py', 'check'])
    assert r.returncode == 0, r.stderr
    assert "Self-sufficiency check passed" in r.stdout


def test_tester_exposes_persist_flags():
    """--persist/--pg-schema/--reset-state — контракт CLI (journal вызывается из движка)."""
    r = _run(['-m', 'strategies.trend_sma.self_tester', '--help'])
    assert r.returncode == 0, r.stderr
    for flag in ('--persist', '--pg-schema', '--reset-state'):
        assert flag in r.stdout, f"{flag} отсутствует в CLI"


def test_runner_is_thin_wrapper():
    """Второй реализации быть не должно: runner = self_tester.main."""
    from strategies.trend_sma import runner, self_tester
    assert runner._self_tester_main is self_tester.main
    assert 'position_manager' not in open(runner.__file__).read()
