import sys, logging
sys.path.insert(0, '/home/user/projects/tqa-framework')
logging.basicConfig(level=logging.INFO, format='%(message)s')
from tqa_framework.backtesters import WhaleBacktester

class PgStub:
    def ensure_schemas(self): pass
    def ensure_tables_backtest(self): pass
    def save_summary(self, s): return 0
    def save_trades_batch(self, t, i): pass
    def save_equity_points(self, e, i): pass

b = WhaleBacktester(
    tickers=[{"symbol": "*", "tf": 60, "risk_pct": 0.03}],
    days=181, risk_pct=0.03, tf_minutes=60,
    strategy_name="whale_hourly_imbalance",
    strategy_params={"th":0.3,"sl_pct":0.05,"max_pos":5,"comm":0.0005},
    initial_equity=1000.0, pg=PgStub(),
    ch_host="http://10.0.0.60:8123", ch_db="crypto",
    max_conc=5, save_results=False,
)
b.end_time_override = "2025-07-01 00:00:00"
r = b.run()
s = r["summary"]
print(f"\n=== RESULT M5-MTM ===\nEQ {s['end_equity']} ret {s['total_return']}% DD {s['mdd']}% WR {s['win_rate']}% PF {s['profit_factor']} trades {s['total_trades']} calmar {s['calmar_ratio']} CAGR {s['cagr']}%")