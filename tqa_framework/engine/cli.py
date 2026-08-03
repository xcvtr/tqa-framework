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
    bt.add_argument("--risk-pct", type=float, default=2.0)
    bt.add_argument("--tf", type=int, default=60, help="Detect timeframe (min)")
    bt.add_argument("--strategy", required=True)
    bt.add_argument("--strategy-path", help="Path to strategies/ directory")
    bt.add_argument("--params", help="JSON params for strategy")
    bt.add_argument("--equity", type=float, default=100_000.0, help="Initial equity")
    bt.add_argument("--max-conc", type=int, default=6, help="Max concurrent positions")

    # grid
    gd = sub.add_parser("grid", help="Sweep parameters")
    gd.add_argument("--strategy", required=True)
    gd.add_argument("--strategy-path", help="Path to strategies/ directory")
    gd.add_argument("--params", required=True, help='JSON: {"param": [values]}')
    gd.add_argument("--tickers", default="MM,GZ")
    gd.add_argument("--days", type=int, default=365)
    gd.add_argument("--tf", type=int, default=60)
    gd.add_argument("--risk-pct", type=float, default=2.0)
    gd.add_argument("--ch-db", default="moex")
    gd.add_argument("--equity", type=float, default=100_000.0)

    # paper trader
    pt = sub.add_parser("paper", help="Run paper trader")
    pt.add_argument("--strategy", required=True)
    pt.add_argument("--executor", default="mock",
                    choices=["mock", "binance", "alor", "mt5"])
    pt.add_argument("--mode", default="tick",
                    choices=["tick", "detect", "both"])
    pt.add_argument("--config", help="Path to YAML config file")

    # results
    rs = sub.add_parser("results", help="Show backtest results")
    rs.add_argument("--limit", type=int, default=10, help="How many runs")
    rs.add_argument("--strategy", help="Filter by strategy name")
    rs.add_argument("--id", type=int, help="Show details of specific run")
    rs.add_argument("--trades", action="store_true", help="Show trades for a run")
    rs.add_argument("--top", action="store_true", help="Show best runs by Calmar")

    return parser


def cmd_backtest(args):
    """Запустить бэктест."""
    from tqa_framework.engine.pg_state import PGState
    from tqa_framework.engine.backtester import Backtester

    pg = PGState(pg_url=args.pg_url)

    tickers = [
        {"symbol": s.strip(), "tf": args.tf, "risk_pct": args.risk_pct}
        for s in args.tickers.split(",")
    ]

    params = {}
    if args.params:
        params = json.loads(args.params)

    bt = Backtester(
        tickers=tickers,
        days=args.days,
        risk_pct=args.risk_pct,
        tf_minutes=args.tf,
        strategy_name=args.strategy,
        strategy_params=params,
        initial_equity=args.equity,
        pg=pg,
        ch_host=args.ch_host,
        ch_db=args.ch_db,
        max_conc=args.max_conc,
        strategy_path=args.strategy_path,
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
    from tqa_framework.engine.pg_state import PGState
    from tqa_framework.engine.backtester import Backtester
    import itertools

    pg = PGState(pg_url=args.pg_url)
    params_grid = json.loads(args.params)

    tickers = [
        {"symbol": s.strip(), "tf": args.tf, "risk_pct": args.risk_pct}
        for s in args.tickers.split(",")
    ]

    keys = list(params_grid.keys())
    values_list = list(params_grid.values())

    best = {"calmar": -999, "params": {}}
    total = 1
    for v in values_list:
        total *= len(v)

    print(f"Grid sweep: {total} комбинаций по {args.strategy}")
    print(f"  keys={keys}")
    print()

    for idx, combo in enumerate(itertools.product(*values_list)):
        params = dict(zip(keys, combo))
        print(f"  [{idx+1}/{total}] {params}", end="")

        bt = Backtester(
            tickers=tickers,
            days=args.days,
            risk_pct=args.risk_pct,
            tf_minutes=args.tf,
            strategy_name=args.strategy,
            strategy_params=params,
            initial_equity=args.equity,
            pg=pg,
            ch_host=args.ch_host,
            ch_db=args.ch_db,
            strategy_path=args.strategy_path,
        )
        result = bt.run()
        calmar = result["summary"]["calmar_ratio"]
        print(f" → Calmar={calmar:.2f}")

        if calmar > best["calmar"]:
            best = {"calmar": calmar, "params": params}

    print("\n" + "=" * 60)
    print(f"  Лучшая комбинация: {best['params']}")
    print(f"  Calmar:            {best['calmar']:.2f}")
    print("=" * 60)


def cmd_paper(args):
    """Запустить paper trader."""
    logger.info("Paper trader: strategy=%s executor=%s mode=%s",
                args.strategy, args.executor, args.mode)
    logger.info("TODO: реализовать paper trader loop")


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
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
