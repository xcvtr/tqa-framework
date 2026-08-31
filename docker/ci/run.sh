#!/usr/bin/env bash
# CI pipeline для tqa-framework
# Проверка изменений → сборка → бэктест → сравнение с baseline
# Использование: ./docker/ci/run.sh [--force]

set -euo pipefail
cd "$(dirname "$0")/../.."  # → project root

PROJECT="tqa-framework"
LOG="/tmp/${PROJECT}_ci.log"
MAX_RUNS_KEEP=20

# --- 1. Git sync ---
if [ "${1:-}" != "--force" ]; then
    git fetch origin main 2>/dev/null || git fetch origin master 2>/dev/null || true
    HEAD=$(git rev-parse HEAD)
    ORIGIN=$(git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "$HEAD")
    if [ "$HEAD" = "$ORIGIN" ]; then
        echo "[CI] No changes. HEAD == origin/main ($HEAD)" | tee "$LOG"
        exit 0
    fi
    echo "[CI] New commits detected. Pulling..."
    git pull --ff-only origin main 2>/dev/null || git pull --ff-only origin master 2>/dev/null || true
fi

# --- 2. Build ---
echo "[CI] Building Docker image..."
cd docker
docker compose build app 2>&1 | tee -a "$LOG"

# --- 3. Ensure PG is up ---
./pg.sh start 2>/dev/null || true
echo "[CI] Waiting for PG..."
sleep 3

# --- 4. Run backtest ---
echo "[CI] Running backtest..."
START_TIME=$(date +%s)
docker compose run --rm app 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}
END_TIME=$(date +%s)
echo "[CI] Backtest exit code: $EXIT_CODE (took $((END_TIME - START_TIME))s)" | tee -a "$LOG"

# --- 5. Compare with previous baseline ---
echo "[CI] Comparing with previous run..."
LATEST=$(./pg.sh psql -At -c "
    SELECT id, total_return, mdd, profit_factor, total_trades, created_at
    FROM backtest.summary
    WHERE tickers @> ARRAY['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']
    ORDER BY created_at DESC LIMIT 2
" 2>/dev/null)

if [ -z "$LATEST" ]; then
    echo "[CI] No results in PG — first run, no comparison possible." | tee -a "$LOG"
    exit "$EXIT_CODE"
fi

# Parse: first row = current run, second row = previous baseline
CURRENT=$(echo "$LATEST" | sed -n '1p')
PREVIOUS=$(echo "$LATEST" | sed -n '2p')

if [ -n "$CURRENT" ] && [ -n "$PREVIOUS" ]; then
    # Extract fields: id|return|mdd|pf|trades|created_at
    CURR_RET=$(echo "$CURRENT" | cut -d'|' -f2)
    PREV_RET=$(echo "$PREVIOUS" | cut -d'|' -f2)

    if [ -n "$CURR_RET" ] && [ -n "$PREV_RET" ] && [ "$PREV_RET" != "0" ]; then
        REGRESSION=$(python3 -c "prev=float('$PREV_RET'); curr=float('$CURR_RET'); change=(curr-prev)/abs(prev)*100 if prev!=0 else 0; print(f'{change:.2f}')")
        echo "[CI] Previous return: ${PREV_RET}% | Current: ${CURR_RET}% | Change: ${REGRESSION}%" | tee -a "$LOG"

        # Check for regression
        if python3 -c "exit(0 if float('$REGRESSION') < -5 else 1)" 2>/dev/null; then
            echo "[CI] ⚠️ REGRESSION DETECTED: ${REGRESSION}% (threshold: -5%)" | tee -a "$LOG"
            # Matrix alert via hermes
            hermes send \
                "⚠️ CI Regression: ${PROJECT}\nReturn: ${PREV_RET}% → ${CURR_RET}% (${REGRESSION}% change)\nExit: $EXIT_CODE\nLog: $LOG" \
                --platform matrix 2>/dev/null || true
        else
            echo "[CI] ✅ No significant regression." | tee -a "$LOG"
        fi
    fi
fi

# Clean up old runs (keep last MAX_RUNS_KEEP)
./pg.sh psql -c "
    DELETE FROM backtest.summary
    WHERE id NOT IN (
        SELECT id FROM backtest.summary
        ORDER BY created_at DESC LIMIT $MAX_RUNS_KEEP
    );
" 2>/dev/null || true

exit "$EXIT_CODE"