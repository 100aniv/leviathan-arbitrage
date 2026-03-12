# Runbook 08 — 서버 크래시 복구 (Server Crash Recovery)

**Severity:** CRITICAL
**SLA:** 감지 즉시. 초기 대응 5분 이내. 완전 복구 45분 이내.
**적용 버전:** Docker Compose 8컨테이너, Python AsyncIO 엔진, TimescaleDB + Redis

---

## 장애 유형 분류

| 유형 | 증상 | 대응 섹션 |
|------|------|-----------|
| OOM (메모리 부족) | Killed 로그, dmesg oom | [Section A](#A-OOM-메모리-부족-복구) |
| 애플리케이션 크래시 | Traceback, CRITICAL 로그 | [Section B](#B-애플리케이션-크래시-복구) |
| 인프라 의존성 장애 | Redis/DB 연결 거부 | [Section C](#C-인프라-의존성-복구) |
| 설정 오류 | KeyError, ENGINE_ENV 오류 | [Section D](#D-설정-오류-복구) |

---

## 증상 확인 (1단계)

```bash
# 전체 컨테이너 상태 확인
docker compose ps

# 예상 비정상 출력:
# leviathan-engine   Exited (1) 3 minutes ago
# leviathan-engine   Restarting (2) 45 seconds ago

# 엔진 종료 원인 확인 (마지막 100줄)
docker compose logs leviathan-engine --tail=100 | \
  grep -E "ERROR|CRITICAL|Traceback|Killed|OOM|CancelledError"

# 시스템 리소스 확인
free -h
df -h /
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## 장애 유형 판단 (2단계)

```bash
# OOM Killer 발동 여부 확인
dmesg | tail -30 | grep -iE "oom|killed|out of memory"
# 또는
journalctl -k --since="1 hour ago" | grep -iE "oom|killed"

# 크래시 종류 분류:
# "oom-kill event" 또는 "Killed process" → Section A (OOM)
# "Traceback" 또는 "asyncpg" → Section B (앱 크래시)
# "ConnectionRefusedError: redis" / "PostgresConnectionError" → Section C (인프라)
# "KeyError" / "ValidationError" / "ENGINE_ENV" → Section D (설정)
```

---

## A. OOM (메모리 부족) 복구

```bash
# 1. 메모리 사용 이력 확인
docker compose logs leviathan-engine --tail=200 | \
  grep -E "MemoryError|OOM|Killed|memory"

# 2. 불필요한 컨테이너 일시 중지 (여유 확보)
docker compose stop leviathan-grafana leviathan-prometheus

# 3. 엔진 재시작
docker compose start leviathan-engine
sleep 15

# 4. 메모리 안정 여부 모니터링 (2분간)
watch -n 10 'docker stats --no-stream leviathan-engine \
  --format "{{.Name}}: {{.MemUsage}} / {{.MemPerc}}"'

# 5. 안정적이면 모니터링 컨테이너 재시작
docker compose start leviathan-grafana leviathan-prometheus
```

**영구 해결책 (재발 방지)**:
```bash
# docker-compose.yml에서 메모리 한도 조정
# deploy:
#   resources:
#     limits:
#       memory: 4g    # 기본 2g → 서버 여유 RAM 확인 후 조정

# 엔진 워커 수 감소
# engine/.env: ENGINE_WORKERS=2
docker compose restart leviathan-engine
```

---

## B. 애플리케이션 크래시 복구

### B-1: 크래시 원인 분석

```bash
# 전체 Traceback 수집
docker compose logs leviathan-engine --since=2h 2>&1 | \
  grep -A 15 "Traceback\|CRITICAL" > /tmp/crash_$(date +%Y%m%d_%H%M%S).log
cat /tmp/crash_*.log | tail -60

# 일반적인 크래시 원인별 대응:
# "asyncio.exceptions.CancelledError" → 정상 종료 신호 (단순 재시작)
# "ConnectionRefusedError: [redis]"   → Section C-1 (Redis 복구 먼저)
# "asyncpg.PostgresConnectionError"   → Section C-2 (DB 복구 먼저)
# "KeyError: 'binance'"               → Section D (설정 오류)
# "RuntimeError: Session is closed"  → 단순 재시작 (asyncio 세션 누수)
```

### B-2: 단순 재시작

```bash
# 1. 포지션 확인 (재시작 전 미체결 주문 유무 파악)
curl -s -m 5 http://localhost:8000/health || echo "Engine down — skipping"

docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT COUNT(*) AS open_orders
FROM execution_log
WHERE status = 'SUBMITTED'
  AND ts > NOW() - INTERVAL '10 minutes';
" 2>/dev/null
# 0이면 안전하게 재시작

# 2. 엔진 재시작
docker compose restart leviathan-engine

# 3. 30초 후 헬스 확인
sleep 30 && curl -s http://localhost:8000/health

# 4. Shadow 메트릭 정상 여부
curl -s http://localhost:8000/api/v1/shadow/stats | python3 -m json.tool
```

### B-3: 이미지 재빌드 (반복 크래시 시)

```bash
# 코드 변경 후 배포 또는 반복 크래시 시 사용
docker compose build leviathan-engine --no-cache

docker compose up -d leviathan-engine

# 빌드 후 시작 로그 확인
timeout 60 bash -c '
  until docker compose logs --tail=10 leviathan-engine 2>/dev/null \
    | grep -q "engine_ready"; do
    sleep 5
  done && echo "Engine READY"
'
```

---

## C. 인프라 의존성 복구

### C-1: Redis 복구

```bash
# 상태 확인
docker compose ps leviathan-redis
docker compose logs leviathan-redis --tail=50

# 재시작
docker compose restart leviathan-redis

# 연결 테스트
docker compose exec redis redis-cli ping
# 응답: PONG

# Redis 복구 후 엔진 재시작
docker compose restart leviathan-engine
```

### C-2: TimescaleDB 복구

```bash
# 상태 확인
docker compose ps leviathan-timescaledb
docker compose logs leviathan-timescaledb --tail=50

# 재시작
docker compose restart leviathan-timescaledb

# 초기화 대기 후 연결 테스트
sleep 30 && docker compose exec timescaledb \
  psql -U leviathan -d leviathan -c "SELECT NOW();"

# DB 복구 후 엔진 재시작
docker compose restart leviathan-engine
```

### C-3: DB 데이터 손상 시 백업 복구

```bash
# 1. 현재 DB 크기 및 무결성 확인
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT pg_size_pretty(pg_database_size('leviathan')), now();"

# 2. 백업 파일 목록 확인
docker run --rm -v leviathan_db_backups:/backups alpine ls -lht /backups/

# 3. 엔진 중지 후 복구 (데이터 손실 주의 — 반드시 운영자 승인 필요)
docker compose stop leviathan-engine
docker compose exec timescaledb psql -U leviathan \
  -c "DROP DATABASE IF EXISTS leviathan;"
docker compose exec timescaledb psql -U leviathan \
  -c "CREATE DATABASE leviathan;"
docker compose exec timescaledb pg_restore \
  -U leviathan -d leviathan \
  -Fc /backups/leviathan_<LATEST_BACKUP>.dump

# 4. 복구 후 엔진 재시작
docker compose start leviathan-engine
```

**상세 절차는 Runbook 04 (Database Recovery)를 참조.**

---

## D. 설정 오류 복구

```bash
# 환경 변수 유효성 확인
docker compose exec leviathan-engine python3 -c \
  "from src.core.settings import Settings; s = Settings(); print('OK:', s.engine_env)"

# 일반적인 설정 오류:
# ENGINE_ENV=development → "dev" 사용 (development 금지)
# EXECUTION_MODE=live without LiveGate → "paper" 또는 "shadow"로 변경
# DATABASE_URL 미설정 → postgresql+asyncpg://leviathan:leviathan@timescaledb:5432/leviathan

# engine/.env 수정
vim engine/.env

# 수정 후 재시작
docker compose restart leviathan-engine

# 설정 적용 확인
docker compose exec leviathan-engine env | \
  grep -E "ENGINE_ENV|EXECUTION_MODE|DATA_MODE"
```

---

## 데이터 정합성 확인

엔진 재시작 후 최근 체결 기록과 잔고를 반드시 확인한다.

```bash
# 1. 최근 30분 체결 기록 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, symbol, status, COUNT(*) AS cnt
FROM execution_log
WHERE ts > NOW() - INTERVAL '30 minutes'
GROUP BY exchange_id, symbol, status
ORDER BY cnt DESC;
" 2>/dev/null

# 2. 10개 거래소 데이터 수신 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, MAX(ts) AS last_tick, NOW() - MAX(ts) AS gap
FROM market_ticks
GROUP BY exchange_id
ORDER BY gap DESC;
" 2>/dev/null
# 기대: 모든 거래소 gap < 1분

# 3. 미체결 주문 잔여 여부
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, symbol, status, ts
FROM execution_log
WHERE status = 'SUBMITTED'
  AND ts > NOW() - INTERVAL '1 hour'
ORDER BY ts DESC;
" 2>/dev/null
# 결과 존재 시 → Runbook 09 (Position Recovery) 참조
```

---

## Telegram 알림 확인

```bash
# Telegram 알림 정상 수신 여부 확인
docker compose logs leviathan-engine | \
  grep -E "telegram|notify|alert" | tail -10

# 수동 테스트 알림 전송
curl -s -X POST http://localhost:8000/debug/test-alert \
  -H "Authorization: Bearer $JWT_TOKEN" || echo "API 응답 없음 (엔진 재시작 중일 수 있음)"
```

---

## 복구 완료 확인 체크리스트

```bash
echo "=== Runbook 08 복구 완료 확인 ==="

# 1. 모든 컨테이너 healthy
docker compose ps --format "table {{.Name}}\t{{.Status}}"
docker compose ps | grep -v "healthy" | grep -v "NAME" \
  && echo "FAIL: unhealthy 컨테이너 존재" || echo "PASS: 모든 컨테이너 healthy"

# 2. API 정상 응답
curl -s -m 5 http://localhost:8000/health | grep -q "healthy" \
  && echo "PASS: API 정상" || echo "FAIL: API 응답 없음"

# 3. Kill Switch 비활성
curl -s http://localhost:8000/risk/metrics | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
  print('PASS: Kill Switch 비활성' if not d.get('kill_switch_active') \
  else 'WARN: Kill Switch 활성 — 수동 해제 필요')"

# 4. Shadow 메트릭 존재
curl -s http://localhost:8000/api/v1/shadow/stats | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print('PASS: Shadow 메트릭 정상' if 'total_trades' in d \
  else 'WARN: Shadow 메트릭 초기화 중 (정상 대기 필요)')"

# 5. 최근 5분 CRITICAL 에러 없음
docker compose logs leviathan-engine --since=5m 2>&1 | \
  grep -q "CRITICAL" \
  && echo "WARN: CRITICAL 에러 존재 — 로그 확인 필요" \
  || echo "PASS: CRITICAL 에러 없음"
```

---

## 포스트 모템 체크리스트

크래시 복구 완료 후 반드시 작성:

```
[ ] 발생 시각 및 감지 방법 기록
[ ] 크래시 원인 (OOM / 앱 크래시 / 인프라 / 설정) 확정
[ ] 크래시 시점 활성 포지션 및 영향 금액 확인
[ ] 영향 범위 (전체 중단 / 특정 전략 / 데이터 수집만) 기록
[ ] 복구 소요 시간 기록
[ ] 재발 방지 조치 확정 (메모리 한도 / 코드 수정 / 설정 변경)
[ ] 신규 장애 패턴 발견 시 SSOT.md Critical Architecture Notes 업데이트
[ ] Telegram 알림이 정상 수신됐는지 확인 (크래시 감지 채널)
[ ] 포지션 불일치 존재 시 Runbook 09 병행 실행
```

---

## 에스컬레이션

| 상황 | 조치 |
|------|------|
| 15분 내 복구 불가 | 개발팀 긴급 연락 |
| DB 데이터 손상 확인 | DBA 호출, Runbook 04 적용 |
| 코드 버그로 반복 크래시 | 긴급 핫픽스 PR, 배포 |
| 크래시 시점 활성 포지션 존재 | Runbook 09 (Position Recovery) 즉시 병행 |

---

## References

- Position Recovery: `engine/docs/runbooks/09_position_recovery.md`
- Database Recovery: `engine/docs/runbooks/04_database_recovery.md`
- Kill Switch: `engine/docs/runbooks/01_kill_switch_recovery.md`
- Incident Response: `engine/docs/runbooks/07_incident_response.md`
- Operations Guide: `docs/operations/operations-guide.md`
