"""CLI entry point.

Примеры:
    python -m engine.cli backtest --tickers GD,GZ --strategy dragon --tf 60
    python -m engine.cli backtest --tickers EURUSD,GBPUSD --strategy fx_top --db forex
    python -m engine.cli grid --strategy dragon --params '{"tf": [3,5,10]}'
    python -m engine.cli paper --strategy dragon --executor binance
"""
from __future__ import annotations

import argparse
import sys
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tqa")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tqa-framework",
        description="Trading framework — crypto, forex, MOEX",
    )
    parser.add_argument("--pg-url", help="PG URL (default: из PG_URL env или localhost:5433/tqa)")
    parser.add_argument("--ch-host", default="http://10.0.0.60:8123", help="ClickHouse URL")
    parser.add_argument("--ch-db", default="moex", help="ClickHouse DB (moex/forex)")

    sub = parser.add_subparsers(dest="mode", required=True)

    # backtest
    bt = sub.add_parser("backtest", help="Run backtest")
    bt.add_argument("--tickers", required=True, help="Comma-separated tickers")
    bt.add_argument("--days", type=int, default=365)
    bt.add_argument("--risk-pct", type=float, default=0.15)
    bt.add_argument("--tf", type=int, default=60, help="Detect timeframe (min)")
    bt.add_argument("--strategy", help="Strategy name (Python detect/tick)")
    bt.add_argument("--strategy-yaml", help="Path to .strategy.yaml (declarative engine)")
    bt.add_argument("--strategy-path", help="Path to strategies/ directory")
    bt.add_argument("--params", help="JSON params for strategy; для YAML — {{param}} template vars")
    bt.add_argument("--equity", type=float, default=100_000.0, help="Initial equity")
    bt.add_argument("--max-conc", type=int, default=6, help="Max concurrent positions")
    bt.add_argument("--backtester", default="default",
                    choices=["default", "lsr_cross"],
                    help="Backtester implementation: 'default' (universal) or 'lsr_cross' (specialized)")

    # grid
    gd = sub.add_parser("grid", help="Sweep parameters")
    gd.add_argument("--strategy", help="Strategy name (Python detect/tick)")
    gd.add_argument("--strategy-yaml", help="Path to .strategy.yaml (declarative engine)")
    gd.add_argument("--strategy-path", help="Path to strategies/ directory")
    gd.add_argument("--params", help='JSON: {"param": [values]} — для YAML опционально, если sweep секция в YAML')
    gd.add_argument("--tickers", default="MM,GZ")
    gd.add_argument("--days", type=int, default=365)
    gd.add_argument("--tf", type=int, default=60)
    gd.add_argument("--risk-pct", type=float, default=0.15)
    gd.add_argument("--ch-db", default="moex")
    gd.add_argument("--equity", type=float, default=100_000.0)

    # paper trader
    pt = sub.add_parser("paper", help="Run paper trader")
    pt.add_argument("--strategy", required=True)
    pt.add_argument("--executor", default="mock",
                    choices=["mock", "binance", "alor", "mt5"])
    pt.add_argument("--mode", default="tick", dest="paper_mode",
                    choices=["tick", "detect", "both"])
    pt.add_argument("--config", help="Path to YAML config file")
    pt.add_argument("--ch-db", default="crypto", help="ClickHouse DB (default: crypto)")
    pt.add_argument("--pg-url", default="", help="PG connection URL for crypto DB")
    pt.add_argument("--dry-run", action="store_true",
                    help="Scratch mode: use strategies_replay schema, never touch live state")
    pt.add_argument("--replay-end", default="",
                    help="ISO timestamp — run detect+tick up to this time (dry-run only)")
    pt.add_argument("--scratch-prefix", default="strategies_replay",
                    help="Schema prefix for scratch tables (default: strategies_replay)")
    pt.add_argument("--equity", type=float, default=475.0,
                    help="Initial equity for scratch seed (default: 475)")
    pt.add_argument("--symbols", default="",
                    help="Override symbols (comma-separated)")
    pt.add_argument("--replay", default="",
                    help="JSON file of pending events [{ts, symbol, direction} ...] for dry-run replay")
    pt.add_argument("--replay-start", default="",
                    help="ISO timestamp — replay start bound (default: 7 days before replay-end)")

    # results
    rs = sub.add_parser("results", help="Show backtest results")
    rs.add_argument("--limit", type=int, default=10, help="How many runs")
    rs.add_argument("--strategy", help="Filter by strategy name")
    rs.add_argument("--id", type=int, help="Show details of specific run")
    rs.add_argument("--trades", action="store_true", help="Show trades for a run")
    rs.add_argument("--top", action="store_true", help="Show best runs by Calmar")

    # strategy
    st = sub.add_parser("strategy", help="Manage YAML strategies in PG")
    st_sub = st.add_subparsers(dest="strategy_action", required=True)

    st_create = st_sub.add_parser("create", help="Upload YAML file to PG")
    st_create.add_argument("name", help="Strategy name")
    st_create.add_argument("file", help="Path to .yaml file")

    st_get = st_sub.add_parser("get", help="Show YAML from PG")
    st_get.add_argument("name", help="Strategy name")

    st_list = st_sub.add_parser("list", help="List all strategies in PG")

    return parser


def cmd_backtest(args):
    """Запустить бэктест."""
    # Валидация: --strategy и --strategy-yaml взаимоисключающие
    if args.strategy and args.strategy_yaml:
        print("Ошибка: --strategy и --strategy-yaml взаимоисключающие", file=sys.stderr)
        sys.exit(1)
    if not args.strategy and not args.strategy_yaml:
        print("Ошибка: укажите --strategy или --strategy-yaml", file=sys.stderr)
        sys.exit(1)

    from tqa_framework.engine.pg_state import PGState

    pg = PGState(pg_url=args.pg_url)

    params = {}
    if args.params:
        params = json.loads(args.params)

    # ── Загрузка go/ms/sp из PG futures.ticker_specs ──
    _ticker_list = [s.strip() for s in args.tickers.split(",")]
    with pg.conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, go, min_step, step_price FROM futures.ticker_specs WHERE ticker = ANY(%s)",
            (_ticker_list,),
        )
        _spec_rows = cur.fetchall()
    _spec_map = {r[0]: {"go": float(r[1]), "ms": float(r[2]), "sp": float(r[3])} for r in _spec_rows}

    tickers = []
    for s in _ticker_list:
        t = {"symbol": s, "tf": args.tf, "risk_pct": args.risk_pct}
        spec = _spec_map.get(s)
        if spec:
            t["go"] = spec["go"]
            t["ms"] = spec["ms"]
            t["sp"] = spec["sp"]
        tickers.append(t)
    # ── Загрузка YAML params для специализированных бэктестеров ──
    # Если strategy_yaml=lsr_cross — грузим close/risk params из YAML и
    # используем LsrCrossBacktester (event-driven с 1m hi/lo симуляцией)
    _yaml_strategy = args.strategy_yaml or ""
    if _yaml_strategy and "/" not in _yaml_strategy and not _yaml_strategy.endswith(".yaml"):
        _yaml_raw = pg.load_strategy_yaml(_yaml_strategy)
        if _yaml_raw:
            import yaml as _yl
            _yaml_dict = _yl.safe_load(_yaml_raw)
            if isinstance(_yaml_dict, dict):
                # Close params
                _close = _yaml_dict.get("close", {})
                if isinstance(_close, dict):
                    for _k, _v in _close.items():
                        if _k not in params:
                            params[_k] = _v
                # Risk params (with mapping)
                _risk = _yaml_dict.get("risk", {})
                if isinstance(_risk, dict):
                    _timeout = _risk.get("exit_timeout_hours")
                    if _timeout is not None:
                        params.setdefault("hold_h", _timeout)
                    _pyr = _risk.get("pyramiding", {})
                    if isinstance(_pyr, dict):
                        params.setdefault("pyr_trigger", _pyr.get("trigger", 0.05))
                        params.setdefault("pyr_add", _pyr.get("add_multiplier", 0.5))

    # ── Выбираем бэктестер ──
    # Auto-route lsr_cross to specialized backtester (event-driven 1m hi/lo sim)
    _use_lsr = args.backtester == "lsr_cross"
    if _use_lsr:
        from tqa_framework.backtesters.lsr_cross import LsrCrossBacktester
        BacktesterClass = LsrCrossBacktester
        # Устанавливаем strategy_name для PG консистентности
        if not args.strategy:
            args.strategy = "lsr_cross"
    else:
        from tqa_framework.engine.backtester import Backtester
        BacktesterClass = Backtester

    bt = BacktesterClass(
        tickers=tickers,
        days=args.days,
        risk_pct=args.risk_pct,
        tf_minutes=args.tf,
        strategy_name=args.strategy or "",
        strategy_params=params,
        initial_equity=args.equity,
        pg=pg,
        ch_host=args.ch_host,
        ch_db=args.ch_db,
        max_conc=args.max_conc,
        strategy_path=args.strategy_path,
        strategy_engine_path=_yaml_strategy,
    )

    result = bt.run()
    s = result["summary"]
    print("\n" + "=" * 60)
    print(f"  Стратегия:      {s['strategy']}")
    print(f"  Тикеры:         {', '.join(s['tickers'])}")
    print(f"  TF:             {s['tf']}m")
    print(f"  Период:         {s['days']} дней")
    print(f"  Капитал:        {s['start_equity']:,.0f} → {s['end_equity']:,.0f}")
    print(f"  Доходность:     {s['total_return']:+.2f}%")
    print(f"  MDD:            -{s['mdd']:.2f}%")
    print(f"  Win Rate:       {s['win_rate']:.1f}%")
    print(f"  Profit Factor:  {s['profit_factor']:.2f}")
    print(f"  Сделок:         {s['total_trades']}")
    print(f"  Calmar:         {s['calmar_ratio']:.2f}")
    print("=" * 60)

    # Показать путь к данным
    print(f"\nДанные сохранены в PG: backtest.trades, .equity_curve, .summary")


def cmd_grid(args):
    """Запустить sweep параметров."""
    # Валидация: --strategy и --strategy-yaml взаимоисключающие
    if args.strategy and args.strategy_yaml:
        print("Ошибка: --strategy и --strategy-yaml взаимоисключающие", file=sys.stderr)
        sys.exit(1)
    if not args.strategy and not args.strategy_yaml:
        print("Ошибка: укажите --strategy или --strategy-yaml", file=sys.stderr)
        sys.exit(1)

    from tqa_framework.engine.pg_state import PGState
    from tqa_framework.grid.runner import sweep_python, sweep_yaml, print_results

    pg = PGState(pg_url=args.pg_url)
    params_grid = json.loads(args.params) if args.params else {}

    _ticker_list_grid = [s.strip() for s in args.tickers.split(",")]
    with pg.conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, go, min_step, step_price FROM futures.ticker_specs WHERE ticker = ANY(%s)",
            (_ticker_list_grid,),
        )
        _spec_rows_grid = cur.fetchall()
    _spec_map_grid = {r[0]: {"go": float(r[1]), "ms": float(r[2]), "sp": float(r[3])} for r in _spec_rows_grid}

    tickers = []
    for s in _ticker_list_grid:
        t = {"symbol": s, "tf": args.tf, "risk_pct": args.risk_pct}
        spec = _spec_map_grid.get(s)
        if spec:
            t["go"] = spec["go"]
            t["ms"] = spec["ms"]
            t["sp"] = spec["sp"]
        tickers.append(t)

    if args.strategy:
        results = sweep_python(
            pg=pg,
            tickers=tickers,
            days=args.days,
            risk_pct=args.risk_pct,
            tf_minutes=args.tf,
            strategy_name=args.strategy,
            strategy_path=args.strategy_path or "",
            params_grid=params_grid,
            initial_equity=args.equity,
            ch_host=args.ch_host,
            ch_db=args.ch_db,
        )
    else:
        results = sweep_yaml(
            pg=pg,
            tickers=tickers,
            days=args.days,
            risk_pct=args.risk_pct,
            tf_minutes=args.tf,
            yaml_path=args.strategy_yaml,
            initial_equity=args.equity,
            ch_host=args.ch_host,
            ch_db=args.ch_db,
            sweep_override=params_grid if params_grid else None,
        )

    print_results(results)


def cmd_paper(args):
    """Запустить paper trader — LSR-CROSS detect+tick."""
    from tqa_framework.engine.paper import (
        load_config, run_detect, run_tick,
        ensure_live, ensure_scratch, seed_scratch_state,
        _connect_pg, load_state, DEFAULT_PG,
    )

    import pathlib

    # ── Resolve YAML config ──
    yaml_path = args.config
    if not yaml_path:
        # Try fallback locations
        for candidate in [
            pathlib.Path(__file__).resolve().parent.parent.parent.parent / "lsr_cross.strategy.yaml",
            pathlib.Path.home() / "projects" / "TQA-crypto" / "strategies" / "lsr_cross" / "config.yaml",
        ]:
            if candidate.exists():
                yaml_path = str(candidate)
                break
        if not yaml_path:
            yaml_path = str(pathlib.Path(__file__).resolve().parent.parent.parent / "lsr_cross.strategy.yaml")

    cfg = load_config(yaml_path)

    # ── Override symbols if provided ──
    if args.symbols:
        cfg["symbols"] = [s.strip() for s in args.symbols.split(",")]

    # ── PG connection ──
    pg_cfg = dict(DEFAULT_PG)
    if args.pg_url:
        # Parse URL
        import urllib.parse
        parsed = urllib.parse.urlparse(args.pg_url)
        pg_cfg["host"] = parsed.hostname or pg_cfg["host"]
        pg_cfg["port"] = parsed.port or pg_cfg["port"]
        pg_cfg["dbname"] = parsed.path.lstrip("/") or pg_cfg["dbname"]
        if parsed.username:
            pg_cfg["user"] = parsed.username
        if parsed.password:
            pg_cfg["password"] = parsed.password

    # ── Schema selection ──
    dry_run = args.dry_run
    schema = args.scratch_prefix if dry_run else "strategies"

    if dry_run:
        ensure_scratch(pg_cfg, schema)
        _seed_pg = _connect_pg(pg_cfg)
        _seed_cur = _seed_pg.cursor()
        _seed_cur.execute(
            f"TRUNCATE TABLE {schema}.multi_closed_trades"
        )
        seed_scratch_state(
            _seed_cur,
            schema=schema,
            equity=args.equity,
            balance=args.equity,
        )
        _seed_pg.commit()
        _seed_pg.close()
        logger.info("[DRY-RUN] scratch schema=%s equity=%.2f", schema, args.equity)
    else:
        ensure_live(pg_cfg)

    # ── Dispatch per mode ──
    pg = _connect_pg(pg_cfg)
    pg.autocommit = False
    cur = pg.cursor()

    try:
        if args.replay and dry_run:
            # Replay mode: time-travel over 1m bars, injecting pending events
            from tqa_framework.engine.paper import run_replay
            from datetime import datetime, timedelta, timezone
            import json as _json

            with open(args.replay) as f:
                events = _json.load(f)

            end = datetime.fromisoformat(args.replay_end.replace("Z", "+00:00")) if args.replay_end \
                else datetime.now(timezone.utc)
            start = datetime.fromisoformat(args.replay_start.replace("Z", "+00:00")) if args.replay_start \
                else end - timedelta(days=7)

            logger.info("--- Replay (schema=%s, %s → %s) ---", schema, start, end)
            result = run_replay(cur, cfg, schema, events, start, end)
            pg.commit()
            logger.info("Replay complete: equity=%.2f trades=%d",
                        result["equity"], len(result["trades"]))
            for t in result["trades"]:
                logger.info("  %s %s %s pnl=%+.2f$",
                            t["symbol"], t["direction"], t["reason"], t["pnl_usd"])
        elif args.paper_mode in ("detect", "both"):
            logger.info("--- Running detect (schema=%s) ---", schema)
            added = run_detect(cur, cfg, schema=schema)
            pg.commit()
            logger.info("Detect complete: %d signals added", added)

        if not args.replay and args.paper_mode in ("tick", "both"):
            logger.info("--- Running tick (schema=%s) ---", schema)
            result = run_tick(cur, cfg, schema=schema)
            pg.commit()
            logger.info(
                "Tick complete: opened=%d closed=%d equity=%.2f",
                result["opened"], result["closed"], result["equity"],
            )
            if result["trades"]:
                for t in result["trades"]:
                    logger.info(
                        "  %s %s %s pnl=%+.2f$ (%.2f%%)",
                        t["symbol"], t["direction"], t["reason"],
                        t["pnl_usd"], t["pnl_pct"] * 100,
                    )
    finally:
        cur.close()
        pg.close()

    # ── Final state ──
    pg2 = _connect_pg(pg_cfg)
    cur2 = pg2.cursor()
    equity, peak, balance, positions = load_state(cur2, schema)
    cur2.close()
    pg2.close()

    print(f"\n{'=' * 60}")
    print(f"  Paper Trader: {args.strategy}")
    print(f"  Mode:         {args.paper_mode}")
    print(f"  Schema:       {schema}")
    print(f"  Equity:       {equity:,.2f}$")
    print(f"  Peak:         {peak:,.2f}$")
    print(f"  Balance:      {balance:,.2f}$")
    print(f"  Positions:    {len(positions)}")
    if dry_run:
        print("  [DRY-RUN] scratch tables — live state untouched")
    print(f"{'=' * 60}")


def cmd_results(args):
    """Показать результаты бэктестов из PG."""
    from tqa_framework.engine.pg_state import PGState

    pg = PGState(pg_url=args.pg_url)
    pg.ensure_tables_backtest()

    if args.id:
        # Детали конкретного запуска
        with pg.conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM backtest.summary WHERE id = %s", (args.id,))
            s = cur.fetchone()
            if not s:
                print(f"Run #{args.id} не найден")
                return
            s = dict(s)
            print("\n" + "=" * 60)
            print(f"  Запуск #:       {s['id']}")
            print(f"  Стратегия:      {s['strategy']}")
            print(f"  Тикеры:         {', '.join(s['tickers'])}")
            print(f"  TF:             {s['tf']}m")
            print(f"  Период:         {s['days']} дней")
            print(f"  Параметры:      {json.dumps(s.get('params', {}), ensure_ascii=False)}")
            print(f"  Создан:         {s['created_at']}")
            print(f"  Капитал:        {s['start_equity']:,.0f} → {s['end_equity']:,.0f}")
            print(f"  Доходность:     {s['total_return']:+.2f}%")
            print(f"  MDD:            -{s['mdd']:.2f}%")
            print(f"  Win Rate:       {s['win_rate']:.1f}%")
            print(f"  Profit Factor:  {s['profit_factor']:.2f}")
            print(f"  Сделок:         {s['total_trades']}")
            print(f"  Calmar:         {s['calmar_ratio']:.2f}")
            print("=" * 60)

            if args.trades:
                print(f"\nСделки (первые 20):")
                print(f"{'#':>4} {'Тикер':>8} {'Dir':>6} {'Entry':>10} {'Exit':>10} "
                      f"{'PnL':>10} {'Причина':>10}")
                print("-" * 70)
                cur.execute(
                    "SELECT * FROM backtest.trades "
                    "WHERE strategy = %s ORDER BY entry_time LIMIT 20",
                    (s['strategy'],)
                )
                for t in cur.fetchall():
                    t = dict(t)
                    print(f"{t['id']:>4} {t['ticker']:>8} {t['direction']:>6} "
                          f"{t['entry_price']:>10.1f} {t['exit_price']:>10.1f} "
                          f"{t['pnl']:>+10.1f} {t['exit_reason']:>10}")
        return

    # Список последних запусков
    query = "SELECT * FROM backtest.summary"
    params_list = []
    if args.strategy:
        query += " WHERE strategy = %s"
        params_list.append(args.strategy)
    if args.top:
        query += " ORDER BY calmar_ratio DESC"
    else:
        query += " ORDER BY created_at DESC"
    query += " LIMIT %s"
    params_list.append(args.limit)

    with pg.conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
        cur.execute(query, params_list)
        rows = cur.fetchall()

    if not rows:
        print("Нет результатов")
        return

    print(f"\n{'#':>4} {'Стратегия':>14} {'Тикеры':>20} {'TF':>4} {'Дни':>5} "
          f"{'Return':>8} {'MDD':>7} {'WR':>5} {'PF':>6} {'Сделок':>7} {'Calmar':>7}")
    print("-" * 95)
    for r in rows:
        r = dict(r)
        tickers = ",".join(r['tickers']) if r['tickers'] else ""
        print(f"{r['id']:>4} {r['strategy']:>14} {tickers:>20} "
              f"{r['tf']:>4} {r['days']:>5} "
              f"{r['total_return']:>+7.1f}% {r['mdd']:>6.1f}% "
              f"{r['win_rate']:>4.1f}% {r['profit_factor']:>5.1f} "
              f"{r['total_trades']:>7} {r['calmar_ratio']:>6.1f}")

    print(f"\n  Подробнее: tqa results --id <номер>")


def cmd_strategy(args):
    """Управление стратегиями в PG."""
    from tqa_framework.engine.pg_state import PGState

    pg = PGState(pg_url=args.pg_url)

    if args.strategy_action == "create":
        import pathlib
        yaml_str = pathlib.Path(args.file).read_text()
        # Валидировать перед сохранением
        from tqa_framework.strategy_engine.parser import load_strategy_from_string
        strategy = load_strategy_from_string(yaml_str)
        pg.save_strategy(args.name, yaml_str, version=strategy.version)
        print(f"Стратегия '{args.name}' v{strategy.version} сохранена в PG")

    elif args.strategy_action == "get":
        yaml_str = pg.load_strategy_yaml(args.name)
        if yaml_str is None:
            print(f"Стратегия '{args.name}' не найдена в PG", file=sys.stderr)
            sys.exit(1)
        print(yaml_str)

    elif args.strategy_action == "list":
        rows = pg.list_strategies()
        if not rows:
            print("Нет стратегий в PG")
            return
        print(f"{'Имя':>16} {'Версия':>8} {'Создана':>30} {'Обновлена':>30}")
        print("-" * 90)
        for r in rows:
            print(f"{r['name']:>16} {r['version']:>8} "
                  f"{r['created_at']:%Y-%m-%d %H:%M:%S} "
                  f"{r['updated_at']:%Y-%m-%d %H:%M:%S}")


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "backtest":
        cmd_backtest(args)
    elif args.mode == "grid":
        cmd_grid(args)
    elif args.mode == "paper":
        cmd_paper(args)
    elif args.mode == "results":
        cmd_results(args)
    elif args.mode == "strategy":
        cmd_strategy(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
