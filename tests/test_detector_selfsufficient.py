"""Детектор самодостаточен: только CH-сырьё и код сигнала, никаких БД/исполнителя.

Проверки статические + по приросту sys.modules, чтобы не зависеть от порядка тестов.
"""
import importlib
import sys

DETECTOR_MODULES = [
    "strategies.trend_sma.detect",
    "strategies.trend_sma.data_provider",
]
FORBIDDEN_IMPORTS = ("psycopg2", "tqa_f.state", "broker", "position_manager", "self_tester")


def _fresh(name):
    for mod in [k for k in sys.modules if k.startswith("strategies.trend_sma")]:
        del sys.modules[mod]
    return importlib.import_module(name)


def test_detector_source_is_clean():
    for name in DETECTOR_MODULES:
        mod = _fresh(name)
        src = open(mod.__file__).read()
        for bad in FORBIDDEN_IMPORTS:
            assert bad not in src, f"{name} imports {bad}"


def test_detector_adds_no_db_modules():
    before = set(sys.modules)
    mod = _fresh("strategies.trend_sma.detect")
    new = set(sys.modules) - before
    heavy = {m for m in new if m.split(".")[0] in ("psycopg2", "clickhouse_connect")}
    assert not heavy, f"detect pulled DB drivers: {heavy}"
    assert mod.detect([], {}) == []
