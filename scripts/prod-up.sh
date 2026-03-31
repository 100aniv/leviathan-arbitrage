#!/bin/bash
# LEVIATHAN Production Mode — override.yml 무시하고 base만 사용
# Usage: bash scripts/prod-up.sh [service...]
# Example: bash scripts/prod-up.sh           # 전체
#          bash scripts/prod-up.sh dashboard  # 대시보드만
set -e
cd "$(dirname "$0")/.."
docker compose -f docker-compose.yml up -d "$@"
echo "Production mode started: $*"
docker compose ps --format "{{.Name}}: {{.Status}}"
