# US-042: Telegram 인프라 모니터링 (Docker 24/7)

## Acceptance Criteria
1. engine/src/infra/monitor_daemon.py 생성
2. docker-compose.yml에 monitoring 서비스 추가
3. 5분 헬스체크: Redis, TimescaleDB, WS 연결
4. 이상 시 Telegram 알림
5. docker compose up 후 monitoring 컨테이너 healthy

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/infra/monitor_daemon.py | NEW — 헬스체크 데몬 | Jennie |
| docker-compose.yml | EDIT — monitoring 서비스 추가 | Jennie |
| engine/tests/unit/infra/test_monitor_daemon.py | NEW — 테스트 | Lisa |

## 설계
- `MonitorDaemon` 클래스: asyncio 기반 5분 주기 루프
- 체크 대상: Redis PING, TimescaleDB pg_isready, Engine /health HTTP
- TelegramAlerter 재사용 (engine/src/infra/telegram.py)
- Docker: engine 이미지 재사용, entrypoint만 변경
- 환경변수: MONITOR_INTERVAL_SEC (default 300), MONITOR_CONSECUTIVE_FAILURES (default 3)
