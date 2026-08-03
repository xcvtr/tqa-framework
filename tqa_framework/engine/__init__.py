"""engine — ядро tqa-framework.

Модули:

- exchange_base.py       — ABC: Position, Signal, ExchangeBase
- exchange_mock.py       — Mock биржа на 1m из CH/PG
- exchange_binance.py    — Live Binance API
- exchange_alor.py       — Live MOEX Alor API
- exchange_mt5_bridge.py — Live MT5 bridge (forex/CFD)
- pg_state.py            — PG connect, CRUD, ensure_tables
- risk.py                — common pool, sizing, trend filter
- detect.py              — общий detect (resample, dedup)
- backtester.py          — портфельный бэктестер
- cli.py                 — единый CLI entry point
"""

__version__ = "0.1.0"
