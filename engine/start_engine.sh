#!/bin/bash
# LEVIATHAN Engine Startup Script
# Starts the engine in paper mode with corrected environment variables

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ENGINE_ENV=dev
export EXECUTION_MODE=paper
export DATA_MODE=synthetic

cd "$SCRIPT_DIR"

# Start engine in background, redirect to log file
nohup python -m src.main > "$SCRIPT_DIR/reports/engine_startup.log" 2>&1 &
ENGINE_PID=$!
echo "Engine started with PID: $ENGINE_PID"
echo "$ENGINE_PID" > "$SCRIPT_DIR/reports/engine.pid"
sleep 8

# Health check
echo "=== Health Check ==="
curl -s http://localhost:8000/health

echo ""
echo "=== Status ==="
curl -s http://localhost:8000/api/v1/status

echo ""
echo "=== Strategies ==="
curl -s http://localhost:8000/api/v1/strategies

echo ""
echo "=== Metrics ==="
curl -s http://localhost:8000/metrics | head -10

echo ""
echo "Engine PID: $ENGINE_PID"
echo "Log file: $SCRIPT_DIR/reports/engine_startup.log"
