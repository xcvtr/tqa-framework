"""State-journal: DDL и API на месте, бар в state не попадает."""
import sys
sys.path.insert(0, '/home/user/projects/tqa-m')

from tqa_m.state.pg_state import DDL_STATE, DDL_CLOSED, PaperDB, load_ticker_specs_pg


def test_journal_tables():
    assert "multi_state" in DDL_STATE
    assert "multi_closed_trades" in DDL_CLOSED
    assert "UNIQUE (symbol, direction, ts_open, entry_price)" in DDL_CLOSED


def test_journal_api():
    db = PaperDB(conn=None, schema="futures")
    assert hasattr(db, "ensure") and hasattr(db, "load")
    assert hasattr(db, "save") and hasattr(db, "save_trade")
    assert callable(load_ticker_specs_pg)


def test_state_has_no_bars():
    # контракт: в PG-state только позиции/капитал, bars туда не пишутся
    state = {"equity": 200000.0, "peak": 200000.0, "balance": 200000.0, "positions": {}}
    assert "bars" not in state
