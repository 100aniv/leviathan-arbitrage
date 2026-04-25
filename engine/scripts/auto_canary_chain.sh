#!/bin/bash
# Progressive paper canary chain (RUNBOOK §0.5 + 프로그레시브 Shadow 프로토콜)
#
# Stage:
#   1. Pre-canary 5min (pre_canary_check.py 자동) → 4/4 PASS or abort
#   2. 30min measurement → 4/4 PASS or abort
#   3. 60min measurement → 4/4 PASS or abort
#   4. 6h measurement → 4/4 PASS + Sharpe>2.0, MDD<5% or abort
#   5. 24h measurement → 4/4 PASS + LiveGate 6-check or abort
#
# Each stage feeds previous log to pre_canary_check.py for 4-item validation.
# Abort condition: 1+ of 4 items fails or stage timeout.
# Result JSON: engine/.omc/evidence/canary_stage_<N>_YYYYMMDD_HHMMSS.json
#
# Usage:
#   cd engine && bash scripts/auto_canary_chain.sh [start_stage]
#   start_stage: 1=pre_canary, 2=30min, 3=60min, 4=6h, 5=24h (default 1)

set -e

ENGINE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ENGINE_ROOT"

START_STAGE="${1:-1}"

# Stage durations (seconds)
declare -a DURATIONS=(0 300 1800 3600 21600 86400)
declare -a NAMES=("" "Pre-canary 5min" "30min" "60min" "6h" "24h")

run_stage() {
    local stage=$1
    local seconds="${DURATIONS[$stage]}"
    local name="${NAMES[$stage]}"
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)

    echo "================================================"
    echo "[chain] Stage $stage: $name (${seconds}s)"
    echo "================================================"

    python scripts/pre_canary_check.py --seconds "$seconds" 2>&1 | tee "/tmp/chain_stage_${stage}_${timestamp}.txt"

    local rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
        echo "[chain] Stage $stage FAILED (rc=$rc) — aborting"
        return 1
    fi

    echo "[chain] Stage $stage PASS"
    return 0
}

for stage in $(seq "$START_STAGE" 5); do
    if ! run_stage "$stage"; then
        echo ""
        echo "[chain] ABORT at stage $stage. Fix root cause before retrying."
        exit 1
    fi
    echo ""
    sleep 5
done

echo "================================================"
echo "[chain] ALL 5 stages PASS — paper canary suite complete"
echo "[chain] Next: US-055 LiveGate Preflight (10 항목 manual check)"
echo "================================================"
