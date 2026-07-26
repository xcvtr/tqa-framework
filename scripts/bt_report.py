#!/usr/bin/env python3
"""bt_report — отчёт бэктеста: equity curve + MDD + таблица метрик.

Использование:
    python3 ~/scripts/bt_report.py --id 5 --png -o /tmp/bt.png --send-matrix
    python3 ~/scripts/bt_report.py --strategy dragon --top --png
    python3 ~/scripts/bt_report.py --id 5 --show-trades
"""
import argparse
import json
import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

# ── PG ─────────────────────────────────────────────────────────────


def pg_connect(pg_url=None):
    url = pg_url or os.environ.get(
        "PG_URL", "postgresql://postgres:***@localhost:5433/tqa"
    )
    # Если пароль *** — заменяем на tqa
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
    if top:
        query += " ORDER BY calmar_ratio DESC"
    else:
        query += " ORDER BY created_at DESC"
    query += " LIMIT %s"
    params.append(limit)
    cur.execute(query, params)
    return [dict(r) for r in cur.fetchall()]


def load_equity(cur, strategy):
    cur.execute(
        "SELECT bar_time, equity, drawdown FROM backtest.equity_curve "
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


# ── GRAPHICS ───────────────────────────────────────────────────────


def plot_equity_curve(equity_points, summary):
    """Сгенерировать Plotly equity curve chart → возвращает HTML."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    times = [p["bar_time"] for p in equity_points]
    eqs = [p["equity"] for p in equity_points]
    dds = [p["drawdown"] for p in equity_points]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
    )

    # Equity curve
    fig.add_trace(
        go.Scatter(
            x=times, y=eqs,
            mode="lines",
            name="Equity",
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,212,170,0.1)",
        ),
        row=1, col=1,
    )

    # Начальная линия
    start_eq = summary["start_equity"]
    fig.add_hline(
        y=start_eq,
        line=dict(color="#888", width=1, dash="dash"),
        row=1, col=1,
    )

    # MDD area
    fig.add_trace(
        go.Scatter(
            x=times + times[::-1],
            y=[0] * len(times) + [-d for d in dds[::-1]],
            fill="toself",
            fillcolor="rgba(255,50,50,0.15)",
            line=dict(color="rgba(255,50,50,0.3)", width=1),
            name="Drawdown",
        ),
        row=2, col=1,
    )

    # Layout
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        title=dict(
            text=f"{summary['strategy']} | {','.join(summary['tickers'])} | TF={summary['tf']}m | {summary['days']}d",
            font=dict(size=14, color="#ccc"),
        ),
        margin=dict(l=60, r=30, t=50, b=30),
        height=500,
        showlegend=False,
        hovermode="x unified",
    )

    fig.update_xaxes(
        gridcolor="#333", zerolinecolor="#444",
        rangeslider=dict(visible=False),
    )
    fig.update_yaxes(
        gridcolor="#333", zerolinecolor="#444",
        title=dict(text="Equity", font=dict(size=11)),
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor="#333", zerolinecolor="#444",
        title=dict(text="DD %", font=dict(size=11)),
        row=2, col=1,
    )

    return fig.to_html(include_plotlyjs="cdn")


def render_to_png(html, output_path):
    """HTML → PNG через kaleido или playwright."""
    import plotly.io as pio
    import plotly.offline

    # Парсим HTML обратно в figure — проще через pio
    # Но мы отдали HTML через fig.to_html. pio.read_html работает нестабильно.
    # Альтернатива: сохранить HTML, playwright screenshot.
    # Или: fig.write_image напрямую.

    # Проще: передать fig напрямую, не через HTML.
    # Но plot_equity_curve возвращает HTML. Давайте переделаем.
    pass


def equity_to_fig(equity_points, summary):
    """Вернуть plotly Figure (не HTML) для прямого рендера."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    times = [p["bar_time"] for p in equity_points]
    eqs = [p["equity"] for p in equity_points]
    dds = [p["drawdown"] for p in equity_points]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.7, 0.3],
    )

    # Equity
    fig.add_trace(
        go.Scatter(
            x=times, y=eqs, mode="lines", name="Equity",
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy", fillcolor="rgba(0,212,170,0.08)",
        ),
        row=1, col=1,
    )
    fig.add_hline(
        y=summary["start_equity"],
        line=dict(color="#888", width=1, dash="dash"),
        row=1, col=1,
    )

    # DD
    fig.add_trace(
        go.Scatter(
            x=times + times[::-1],
            y=[0] * len(times) + [-d for d in dds[::-1]],
            fill="toself",
            fillcolor="rgba(255,50,50,0.12)",
            line=dict(color="rgba(255,50,50,0.3)", width=1),
            name="Drawdown",
        ),
        row=2, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        title=dict(
            text=f"{summary['strategy']} | {','.join(summary['tickers'])} | TF={summary['tf']}m | {summary['days']}d",
            font=dict(size=13, color="#aaa"),
            x=0.5,
        ),
        margin=dict(l=50, r=20, t=40, b=20),
        height=480,
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_xaxes(
        gridcolor="#2a2a3e", zerolinecolor="#333",
        title=dict(text="", font=dict(size=10)),
    )
    fig.update_yaxes(
        gridcolor="#2a2a3e", zerolinecolor="#333",
        title=dict(text="Equity", font=dict(size=10)),
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor="#2a2a3e", zerolinecolor="#333",
        title=dict(text="DD %", font=dict(size=10)),
        row=2, col=1,
        range=[-max(dds) * 1.3 if dds else -10, 2],
    )

    return fig


# ── OUTPUT ─────────────────────────────────────────────────────────


def format_metrics(summary):
    """Отформатировать таблицу метрик."""
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

    # ROI за год
    if summary["days"] > 0:
        years = summary["days"] / 365.0
        annual_return = (1 + summary["total_return"] / 100) ** (1 / years) - 1
        lines.append(f"Годовая:    {annual_return * 100:>+10.2f}%")
        sharpe = summary["calmar_ratio"]  # proxy
        lines.append(f"Sharpe≈:    {sharpe:>10.2f}")

    if summary.get("params"):
        try:
            params = summary["params"]
            if isinstance(params, str):
                params = json.loads(params)
            if params:
                lines.append("")
                lines.append(f"Параметры:  {json.dumps(params, ensure_ascii=False)}")
        except Exception:
            pass

    return "\n".join(lines)


# ── MAIN ───────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Backtest report")
    parser.add_argument("--id", type=int, help="Run ID")
    parser.add_argument("--strategy", help="Filter by strategy")
    parser.add_argument("--top", action="store_true", help="Best by Calmar")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--png", action="store_true", help="Render PNG")
    parser.add_argument("-o", "--output", default="/tmp/bt_report.png",
                        help="Output file")
    parser.add_argument("--send-matrix", action="store_true",
                        help="Send to Matrix")
    parser.add_argument("--room",
                        default=os.environ.get("MATRIX_HOME_ROOM", ""),
                        help="Matrix room")
    parser.add_argument("--show-trades", action="store_true",
                        help="Show trades table")
    args = parser.parse_args()

    conn = pg_connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.id:
        summary = load_summary(cur, args.id)
        if not summary:
            print(f"Run #{args.id} not found")
            sys.exit(1)
        _show_detailed(cur, summary, args)
    else:
        rows = load_summaries(cur, args.strategy, args.top, args.limit)
        _show_list(rows)


def _show_detailed(cur, s, args):
    """Показать детальный отчёт по одному запуску."""
    s = dict(s)

    # Метрики текстом
    report = format_metrics(s)
    print(report)

    # Сделки
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

    # Equity curve PNG
    if args.png or args.send_matrix:
        equity = load_equity(cur, s["strategy"])
        if equity:
            fig = equity_to_fig(equity, s)
            output = args.output or f"/tmp/bt_{s['id']}.png"
            try:
                fig.write_image(output, scale=2, width=1000, height=480)
                print(f"\nChart saved: {output}")
                if args.send_matrix:
                    # MEDIA: — Hermes отправит в текущий чат
                    print(f"MEDIA:{output}")
            except Exception as e:
                print(f"PNG error: {e}")
                try:
                    html_out = output.replace(".png", ".html")
                    fig.write_html(html_out)
                    print(f"HTML fallback: {html_out}")
                except Exception as e2:
                    print(f"HTML fallback error: {e2}")
        else:
            print("No equity data")


def _show_list(rows):
    """Показать список запусков."""
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
