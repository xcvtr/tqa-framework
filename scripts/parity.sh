#!/bin/bash
# Гейт паритета: прогон 28 тикеров и сверка с эталоном канона 106.
set -e
cd "$(dirname "$0")/.."

OUT=$(PYTHONPATH=. python -m strategies.trend_sma.self_tester \
  --tickers PT,MX,NG,LK,SV,TT,RI,MG,MM,SG,USDRUBF,BR,CR,SN,MN,IMOEXF,MC,NM,NA,FL,TP,GLDRUBF,CM,SO,YD,HY,AL,VK \
  --start 2026-01-01 --end 2026-11-11 2>&1)

echo "$OUT" | grep -E 'Final equity|ROI:|Max DD|Total trades|Win rate'

echo "$OUT" | python3 -c '
import re, sys
t = sys.stdin.read()
g = lambda p: float(re.search(p, t).group(1).replace(",", ""))
got = dict(trades=g(r"Total trades:\s+(\d+)"), roi=g(r"ROI:\s+([\d.]+)"),
           dd=g(r"Max DD:\s+([\d.]+)"), wr=g(r"Win rate:\s+([\d.]+)"))
# 17.11: loader дописал 11-11 (вечерняя сессия, которой не было в roll) -> 4 сделки.
# Прогон до 11-10 даёт 1377 / 1052.76% — прежний эталон на неполном дне.
# 18.11: честные косты — входной слиппедж (1 тик) + комиссия за обе ноги.
# Было 1381 / 1055.79 / 11.88 / 41.85 — это была недооценка издержек (~2% ROI).
exp = dict(trades=1382, roi=1033.91, dd=12.15, wr=41.03)
bad = [f"{k}: {got[k]} != {exp[k]}" for k in exp if abs(got[k] - exp[k]) > (0 if k == "trades" else 0.01)]
print("PASS" if not bad else "FAIL: " + "; ".join(bad))
sys.exit(1 if bad else 0)
'
