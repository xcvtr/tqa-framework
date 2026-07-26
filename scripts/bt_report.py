#!/usr/bin/env python3
"""bt_report — отчёт бэктеста: equity curve + MTM DD overlay + таблица метрик.

Использование:
    python3 ~/scripts/bt_report.py --id 5 --png --send-matrix
    python3 ~/scripts/bt_report.py --list
"""
import argparse
import json
import os
import sys

import psycopg2
import psycopg2.extras


# ── PG ─────────────────────────────────────────────────────────────


def pg_connect(pg_url=None):
    url = pg_url or os.environ.get(
        "PG_URL", "postgresql://postgres:***@localhost:5433/tqa"
    )
    url = url.replace(":***@", ":tqa@")
    return psycopg2.connect(url)


def load_summary(cur, run_id):
    cur.execute("SELECT * FROM backtest.summary WHERE id = %s", (run_id,))
    return dict(cur.fetchone()) if cur.rowcount else None


def load_summaries(cur, strategy=None, top=False, limit=10):
    query = "SELECT * FROM backtest.summary"
    params = []
    if strategy:
        query += " WHERE strategy = %s"
        params.append(strategy)
    query += " ORDER BY calmar_ratio DESC" if top else " ORDER BY created_at DESC"
    query += " LIMIT %s"
    params.append(limit)
    cur.execute(query, params)
    return [dict(r) for r in cur.fetchall()]


def load_equity(cur, strategy):
    cur.execute(
        "SELECT bar_time, equity, cash_equity, drawdown "
        "FROM backtest.equity_curve "
        "WHERE strategy = %s ORDER BY bar_time",
        (strategy,),
    )
    return [dict(r) for r in cur.fetchall()]


def load_trades(cur, strategy, limit=50):
    cur.execute(
        "SELECT * FROM backtest.trades "
        "WHERE strategy = %s ORDER BY entry_time LIMIT %s",
        (strategy, limit),
    )
    return [dict(r) for r in cur.fetchall()]


# ── CHART ──────────────────────────────────────────────────────────


def equity_to_fig(equity_points, summary):
    """Одна панель: equity (MTM) + cash (реализованная) + DD заливка от пика."""
    import plotly.graph_objects as go

    times = [p["bar_time"] for p in equity_points]
    eqs = [p["equity"] for p in equity_points]
    cash = [p.get("cash_equity", p["equity"]) for p in equity_points]

    # Running peak и drawdown fill
    peak = float("-inf")
    peak_line = []
    dd_bottom = []
    for e in eqs:
        if e > peak:
            peak = e
        peak_line.append(peak)
        dd_bottom.append(peak - (peak - e) * 0.97)  # небольшая база для заливки

    fig = go.Figure()

    # DD fill (от пика до equity)
    fig.add_trace(go.Scatter(
        x=times + times[::-1],
        y=peak_line + [e for e in reversed(eqs)],
        fill="toself",
        fillcolor="rgba(255,50,50,0.10)",
        line=dict(color="rgba(255,50,50,0)", width=0),
        name="Drawdown",
        showlegend=False,
        hoverinfo="skip",
    ))

    # MTM equity (основная линия)
    fig.add_trace(go.Scatter(
        x=times, y=eqs,
        mode="lines",
        name="MTM Equity",
        line=dict(color="#00d4aa", width=2.5),
        hovertemplate="MTM: %{y:,.0f}<extra></extra>",
    ))

    # Cash equity (реализованная)
    fig.add_trace(go.Scatter(
        x=times, y=cash,
        mode="lines",
        name="Cash Equity",
        line=dict(color="#ffa500", width=1.5, dash="dot"),
        hovertemplate="Cash: %{y:,.0f}<extra></extra>",
    ))

    # Peak line (пунктир)
    fig.add_trace(go.Scatter(
        x=times, y=peak_line,
        mode="lines",
        name="Peak",
        line=dict(color="rgba(255,50,50,0.4)", width=1, dash="dash"),
        hovertemplate="Peak: %{y:,.0f}<extra></extra>",
    ))

    # Стартовый капитал
    start_eq = summary["start_equity"]
    fig.add_hline(
        y=start_eq,
        line=dict(color="#888", width=1, dash="dot"),
        annotation_text=f"Start {start_eq:,.0f}",
        annotation_position="bottom right",
    )

    # Аннотация с итогами (в правом верхнем углу)
    max_dd = summary["mdd"]
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.98, y=0.98,
        text=(
            f"<b>{summary['strategy']}</b> | {','.join(summary['tickers'])}<br>"
            f"TF={summary['tf']}m | {summary['days']}d<br>"
            f"Return: <b>{summary['total_return']:+.2f}%</b>"
        ),
        showarrow=False,
        font=dict(size=11, color="#ccc"),
        align="right",
        bgcolor="rgba(26,26,46,0.85)",
        bordercolor="#444",
        borderwidth=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        margin=dict(l=50, r=20, t=30, b=30),
        height=400,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=10),
            bgcolor="rgba(26,26,46,0)",
        ),
    )
    fig.update_xaxes(
        gridcolor="#2a2a3e", zerolinecolor="#333",
        title=dict(text="", font=dict(size=10)),
    )
    fig.update_yaxes(
        gridcolor="#2a2a3e", zerolinecolor="#333",
        title=dict(text="Equity", font=dict(size=10)),
    )

    return fig


# ── OUTPUT ─────────────────────────────────────────────────────────


def format_metrics(summary):
    lines = [
        f"📊 {summary['strategy']} | {','.join(summary['tickers'])} | TF={summary['tf']}m | {summary['days']}d",
        "",
        f"Капитал:    {summary['start_equity']:>10,.0f} → {summary['end_equity']:>10,.0f}",
        f"Return:     {summary['total_return']:>+10.2f}%",
        f"MDD:        {summary['mdd']:>10.2f}%",
        f"Win Rate:   {summary['win_rate']:>10.1f}%",
        f"Profit Fac: {summary['profit_factor']:>10.2f}",
        f"Сделок:     {summary['total_trades']:>10}",
        f"Calmar:     {summary['calmar_ratio']:>10.2f}",
        "",
    ]
    if summary["days"] > 0:
        years = summary["days"] / 365.0
        total = summary["total_return"] / 100
        if total > -1:
            annual_return = (1 + total) ** (1 / years) - 1
            lines.append(f"Годовая:    {annual_return * 100:>+10.2f}%")
        else:
            lines.append(f"Годовая:    < -100%")
    if summary.get("params"):
        try:
            p = summary["params"]
            if isinstance(p, str):
                p = json.loads(p)
            if p:
                lines.append(f"Параметры:  {json.dumps(p, ensure_ascii=False)}")
        except Exception:
            pass
    return "\n".join(lines)


# ── MAIN ───────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Backtest report")
    parser.add_argument("--id", type=int, help="Run ID")
    parser.add_argument("--strategy", help="Filter by strategy")
    parser.add_argument("--top", action="store_true", help="Best by Calmar")
    parser.add_argument("--list", action="store_true", help="List runs")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--png", action="store_true", help="Render PNG")
    parser.add_argument("-o", "--output", default="",
                        help="Output file")
    parser.add_argument("--send-matrix", action="store_true",
                        help="Print MEDIA: path")
    parser.add_argument("--show-trades", action="store_true",
                        help="Show trades table")
    args = parser.parse_args()

    conn = pg_connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.list or (not args.id and not args.strategy and not args.top):
        rows = load_summaries(cur, args.strategy, args.top, args.limit)
        _show_list(rows)
        return

    if args.id:
        s = load_summary(cur, args.id)
        if not s:
            print(f"Run #{args.id} not found")
            sys.exit(1)
    elif args.top or args.strategy:
        rows = load_summaries(cur, args.strategy, args.top, limit=1)
        if not rows:
            print("No results")
            return
        s = rows[0]
    else:
        parser.print_help()
        return

    _show_detailed(cur, s, args)


def _show_detailed(cur, s, args):
    s = dict(s)
    report = format_metrics(s)
    print(report)

    if args.show_trades:
        trades = load_trades(cur, s["strategy"])
        if trades:
            print(f"\n{'Тикер':>8} {'Dir':>6} {'Entry':>10} {'Exit':>10} "
                  f"{'PnL':>10} {'Reason':>10}")
            print("-" * 60)
            for t in trades:
                print(f"{t['ticker']:>8} {t['direction']:>6} "
                      f"{t['entry_price']:>10.1f} {t['exit_price']:>10.1f} "
                      f"{t['pnl']:>+10.1f} {t['exit_reason']:>10}")

    if args.png or args.send_matrix:
        equity = load_equity(cur, s["strategy"])
        if equity:
            fig = equity_to_fig(equity, s)
            out = args.output or f"/tmp/bt_{s['id']}.png"
            try:
                fig.write_image(out, scale=2, width=1000, height=400)
                print(f"\nChart: {out}")
                # Копируем в разрешённую для MEDIA директорию
                media_dir = os.path.expanduser("~/.hermes/browser_screenshots")
                os.makedirs(media_dir, exist_ok=True)
                media_path = os.path.join(media_dir, f"bt_{s['id']}.png")
                import shutil
                shutil.copy2(out, media_path)
                if args.send_matrix:
                    print(f"MEDIA:{media_path}")
            except Exception as e:
                print(f"PNG error: {e}")


def _show_list(rows):
    if not rows:
        print("No results")
        return
    print(f"\n{'#':>4} {'Стратегия':>14} {'Тикеры':>16} {'TF':>4} {'Дни':>5} "
          f"{'Return':>8} {'MDD':>7} {'WR':>5} {'PF':>6} {'Сделок':>7} {'Calmar':>7}")
    print("-" * 90)
    for r in rows:
        r = dict(r)
        tickers = ",".join(r["tickers"]) if r["tickers"] else ""
        print(f"{r['id']:>4} {r['strategy']:>14} {tickers:>16} "
              f"{r['tf']:>4} {r['days']:>5} "
              f"{r['total_return']:>+7.1f}% {r['mdd']:>6.1f}% "
              f"{r['win_rate']:>4.1f}% {r['profit_factor']:>5.1f} "
              f"{r['total_trades']:>7} {r['calmar_ratio']:>6.1f}")
    print(f"\nДетально: bt_report.py --id <номер>")


if __name__ == "__main__":
    main()
