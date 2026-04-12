#!/usr/bin/env bash
# start_canary.sh — 포트 충돌 없이 카나리 재시작
# 사용: ./scripts/start_canary.sh [round_name]
# 예:   ./scripts/start_canary.sh round33s

set -e
ROUND=${1:-"canary"}
LOG_DIR="$(dirname "$0")/../$(cd "$(dirname "$0")/.." && ls -d logs 2>/dev/null || (mkdir -p logs && echo logs))"
LOG="$LOG_DIR/${ROUND}_$(date +%Y%m%d_%H%M%S).log"

echo "[start_canary] Stopping any running engine on port 8000..."
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
pkill -f "src.main" 2>/dev/null || true
sleep 2

# Verify port is free
if lsof -ti:8000 >/dev/null 2>&1; then
    echo "[start_canary] ERROR: Port 8000 still in use after kill. Aborting."
    lsof -i:8000
    exit 1
fi

echo "[start_canary] Starting engine... log=$LOG"
nohup python -m src.main > "$LOG" 2>&1 &
PID=$!
echo "[start_canary] PID=$PID"

# Quick health check after 5 seconds
sleep 5
if ! kill -0 $PID 2>/dev/null; then
    echo "[start_canary] ERROR: Process died immediately. Check log:"
    tail -20 "$LOG"
    exit 1
fi

echo "[start_canary] Engine running. PID=$PID log=$LOG"
