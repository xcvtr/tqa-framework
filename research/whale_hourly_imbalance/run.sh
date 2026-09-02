#!/bin/bash
# Воспроизводимый прогон whale (M5-MTM, IS 2025 H1) — 1 команда.
set -e
cd "$(dirname "$0")"
PY=/home/user/venvs/tqa/main/bin/python3
cd ../../..  # tqa-framework
echo "== whale M5-MTM backtest (2025 H1, risk 3%) =="
$PY /home/user/projects/tqa-framework/research/whale_hourly_imbalance/run_m5_mtm.py