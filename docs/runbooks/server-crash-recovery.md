# Runbook: 서버 크래시 복구

> **카테고리**: 인프라 장애
> **심각도**: P1 (서비스 중단)
> **예상 복구 시간**: 15~45분
> **최종 수정**: 2026-03-11

---

## 증상

- 엔진 컨테이너가 `Exited` 또는 `Restarting (loop)` 상태
- `http://localhost:8000/health` 응답 없음
- Telegram 알림: "Engine connection lost" 또는 "CRITICAL" 레벨 메시지
- 대시보드에서 "연결 끊김" 표시

---

## 즉각 조치 (5분 이내)

### 1단계: 상황 파악

```bash
# 전체 컨테이너 상태 확인
docker compose ps

# 엔진 종료 원인 확인
docker compose logs engine --tail=100 | grep -E "ERROR|CRITICAL|Traceback|Killed|OOM"

# 시스템 리소스 확인
free -h && df -h
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

### 2단계: OOM 여부 판단

```bash
# OOM Killer 로그 확인 (Linux)
dmesg | tail -30 | grep -i "oom\|killed"

# 또는
journalctl -k --since="1 hour ago" | grep -i "oom\|killed"
```

**OOM인 경우** → [§A OOM 복구](#A-OOM-메모리-부족-복구)
**코드 크래시인 경우** → [§B 애플리케이션 크래시 복구](#B-애플리케이션-크래시-복구)
**DB/Redis 연결 오류인 경우** → [§C 인프라 의존성 복구](#C-인프라-의존성-복구)

---

## A. OOM (메모리 부족) 복구

```bash
# 1. 메모리 한도 상황 확인
docker compose logs engine --tail=200 | grep -E "MemoryError|OOM|Killed"

# 2. 불필요한 컨테이너 중지 (임시)
docker compose stop grafana prometheus

# 3. 엔진 재시작
docker compose start engine

# 4. 재시작 후 메모리 모니터링
watch -n 5 'docker stats --no-stream leviathan-engine'

# 5. 메모리 사용량이 안정적이면 Grafana/Prometheus 재시작
docker compose start grafana prometheus
```

**영구 해결책** (재발 방지):
```bash
# docker-compose.yml에서 엔진 메모리 한도 증가 (기본 2GB)
# mem_limit: 4g  (서버 RAM 여유 확인 후)

# 또는 ENGINE_WORKERS 감소
# echo "ENGINE_WORKERS=2" >> .env
docker compose restart engine
```

---

## B. 애플리케이션 크래시 복구

### B-1: 크래시 원인 분석

```bash
# 전체 에러 로그 수집
docker compose logs engine --since=2h 2>&1 | grep -A 10 "Traceback\|CRITICAL" > /tmp/crash_log.txt
cat /tmp/crash_log.txt

# 일반적인 크래시 원인:
# - "asyncio.exceptions.CancelledError" → 정상 종료 (재시작만 필요)
# - "ConnectionRefusedError: redis" → Redis 먼저 복구 (§C 참조)
# - "asyncpg.PostgresConnectionError" → DB 먼저 복구 (§C 참조)
# - "KeyError: 'binance'" → 설정 오류 (§D 참조)
```

### B-2: 단순 재시작

```bash
# 1. 종료된 컨테이너 재시작
docker compose restart engine

# 2. 30초 후 상태 확인
sleep 30 && docker compose ps

# 3. 헬스체크
curl -s http://localhost:8000/health

# 4. Shadow 메트릭 복구 확인
curl -s http://localhost:8000/api/v1/shadow/stats
```

### B-3: 이미지 재빌드 후 재시작

크래시가 반복되거나 코드 변경 후 배포 실패 시:

```bash
# 1. 이미지 재빌드
docker compose build engine --no-cache

# 2. 재시작
docker compose up -d engine

# 3. 빌드 로그에서 에러 확인
docker compose logs engine --tail=50
```

---

## C. 인프라 의존성 복구

### C-1: Redis 복구

```bash
# Redis 상태 확인
docker compose ps redis
docker compose logs redis --tail=50

# Redis 재시작
docker compose restart redis

# 연결 테스트
docker compose exec redis redis-cli ping
# 응답: PONG

# Redis 복구 후 엔진 재시작
docker compose restart engine
```

### C-2: TimescaleDB 복구

```bash
# DB 상태 확인
docker compose ps timescaledb
docker compose logs timescaledb --tail=50

# DB 재시작
docker compose restart timescaledb

# 연결 테스트 (30초 대기 후)
sleep 30 && docker compose exec timescaledb psql -U leviathan -d leviathan -c "SELECT NOW();"

# DB 복구 확인 후 엔진 재시작
docker compose restart engine
```

### C-3: DB 손상 시 백업 복구

```bash
# 1. 현재 DB 상태 확인
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT pg_database_size('leviathan'), now();"

# 2. 백업 파일 목록 확인
docker run --rm -v leviathan_db_backups:/backups alpine ls -lh /backups/

# 3. DB 중지 후 복구 (데이터 손실 주의!)
docker compose stop engine
docker compose exec timescaledb psql -U leviathan -c "DROP DATABASE IF EXISTS leviathan;"
docker compose exec timescaledb psql -U leviathan -c "CREATE DATABASE leviathan;"
docker compose exec timescaledb pg_restore \
  -U leviathan -d leviathan \
  -Fc /backups/leviathan_<latest_backup>.dump

# 4. 엔진 재시작
docker compose start engine
```

---

## D. 설정 오류 복구

```bash
# 환경 변수 유효성 확인
docker compose exec engine python -c "from src.core.settings import Settings; s = Settings(); print('OK:', s.engine_env)"

# 일반적인 설정 오류:
# ENGINE_ENV=development → development 대신 dev 사용
# EXECUTION_MODE=live without LiveGate → shadow로 변경

# .env 수정 후
vim engine/.env

# 재시작
docker compose restart engine
```

---

## 복구 완료 확인 체크리스트

```bash
# 체크리스트 실행
echo "=== 복구 완료 확인 ==="

# 1. 모든 컨테이너 healthy
docker compose ps | grep -v healthy && echo "❌ unhealthy 컨테이너 존재" || echo "✅ 모든 컨테이너 healthy"

# 2. API 응답
curl -s http://localhost:8000/health | grep -q "healthy" && echo "✅ API 정상" || echo "❌ API 응답 없음"

# 3. Shadow 메트릭 존재
curl -s http://localhost:8000/api/v1/shadow/stats | grep -q "total_trades" && echo "✅ Shadow 메트릭 정상" || echo "⚠️ Shadow 메트릭 없음 (초기화 중)"

# 4. Kill Switch 비활성
curl -s http://localhost:8000/risk/metrics | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
  print('✅ Kill Switch 비활성' if not d.get('kill_switch_active') else '⚠️ Kill Switch 활성 (수동 해제 필요)')"

# 5. 에러 로그 없음 (최근 5분)
docker compose logs engine --since=5m 2>&1 | grep -q "CRITICAL" && echo "❌ CRITICAL 에러 존재" || echo "✅ CRITICAL 에러 없음"
```

---

## 복구 후 조치

1. **포스트-모템 작성**: 크래시 원인, 복구 시간, 영향도 기록
2. **SSOT.md 업데이트**: 신규 장애 패턴 발견 시 Critical Architecture Notes에 추가
3. **알림 설정 확인**: Telegram 알림이 정상 수신되었는지 확인
4. **포지션 확인**: 크래시 시점 활성 포지션이 있었다면 [`position-recovery.md`](position-recovery.md) 참조

---

## 에스컬레이션

| 상황 | 조치 |
|------|------|
| 15분 내 복구 불가 | 개발팀 긴급 연락 |
| DB 데이터 손상 | DBA 호출, 백업 복구 |
| 코드 버그로 인한 반복 크래시 | PR 긴급 핫픽스 |
| 실거래 포지션 영향 | `position-recovery.md` 병행 실행 |

*관련 Runbook: [`position-recovery.md`](position-recovery.md) | [`operations-guide.md`](../operations/operations-guide.md)*
