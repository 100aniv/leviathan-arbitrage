#!/bin/bash
# LEVIATHAN .env 통합 확인 스크립트
# engine/.env 삭제 완료 (US-375) — 단일 소스: 루트 .env
set -e
cd "$(dirname "$0")/.."

echo "=== .env 통합 확인 ==="

if [ -f engine/.env ]; then
    echo "ERROR: engine/.env exists — should have been eliminated (US-375)"
    echo "  root .env is the single source of truth. Delete engine/.env."
    exit 1
fi

if [ -f .env ]; then
    echo "OK: root .env exists (single source of truth)"
else
    echo "ERROR: root .env not found"
    exit 1
fi

echo "OK: engine/.env eliminated, root .env present"
