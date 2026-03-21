#!/usr/bin/env bash
# LEVIATHAN Dev봇 (Watchdog + 텔레그램) — 독립 프로세스 래퍼
#
# Dev봇 자체가 watchdog입니다:
#   - tmux "leviathan" 세션 멈춤 감지 → 알림 + 자동 재개
#   - 텔레그램 /go, /phase, /session 등 양방향 명령
#   - Stage 진행 상황 알림
#
# 사용법:
#   bash scripts/watchdog.sh                    # 포그라운드
#   bash scripts/watchdog.sh &                  # 백그라운드
#   tmux split-window -t leviathan 'bash scripts/watchdog.sh'  # tmux 분할
#
# 환경변수 (engine/.env에서 자동 로드):
#   DEV_TELEGRAM_BOT_TOKEN  — Dev봇 토큰
#   DEV_TELEGRAM_CHAT_ID    — 알림 대상 채팅
#   DEV_TELEGRAM_ENABLED    — "true" 필수
#   WATCHDOG_TMUX_SESSION   — 모니터링 대상 (기본: leviathan)
#   WATCHDOG_INTERVAL       — 체크 간격 초 (기본: 5)
#   WATCHDOG_COOLDOWN       — 연속 알림 방지 초 (기본: 30)
#   WATCHDOG_RESUME_DELAY   — 재개 전 대기 초 (기본: 2)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENGINE_DIR="$REPO_ROOT/engine"

# .env 로드
if [[ -f "$ENGINE_DIR/.env" ]]; then
    set -a
    source "$ENGINE_DIR/.env"
    set +a
fi

cd "$ENGINE_DIR"
exec python -m src.infra.telegram_dev_bot
