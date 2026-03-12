# Runbook 07 — 장애 대응 매뉴얼 (Incident Response)

**Severity:** 장애 유형별 상이 (아래 각 섹션 참조)
**SLA:** 감지 2분 이내. 초기 대응 10분 이내. 완전 복구 1~4시간.
**적용 버전:** 거래소 10개 (spot 7 + futures 3), 전략 7개 (futures_futures 포함), Docker 14컨테이너
**컨테이너 목록:** engine, timescaledb, redis, dashboard, nginx, grafana, prometheus, redis-exporter, monitoring, auto-tuner, db-backup, wal-backup, loki, promtail

---

## 장애 유형 색인

| # | 장애 유형 | Severity | 섹션 |
|---|-----------|----------|------|
| 1 | WS 연결 끊김 | HIGH | [Section 1](#1-ws-websocket-연결-끊김) |
| 2 | DB 연결 실패 | HIGH | [Section 2](#2-db-연결-실패) |
| 3 | 거래소 API 장애 | HIGH | [Section 3](#3-거래소-api-장애) |
| 4 | Kill Switch 발동 | CRITICAL | [Section 4](#4-kill-switch-발동) |
| 5 | 메모리 누수 감지 | MEDIUM | [Section 5](#5-메모리-누수-감지) |
| 6 | Docker 컨테이너 장애 | HIGH | [Section 6](#6-docker-컨테이너-장애) |
| 7 | 대시보드 API 응답 없음 | MEDIUM | [Section 7](#7-대시보드--api-응답-없음) |

---

## 1. WS (WebSocket) 연결 끊김

**Severity:** HIGH
**감지 신호:** `ws_disconnect` / `reconnect_attempt` 로그, 수집 중단 > 30초

### 1.1 감지 방법

```bash
# 실시간 WS 상태 로그 확인
docker compose logs --tail=100 leviathan-engine 2>/dev/null | \
  grep -E "ws_disconnect|reconnect_attempt|collector_error|ws_error"

# 거래소별 데이터 수신 간격 확인 (30초 이상 공백 = 끊김)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
    exchange_id,
    MAX(ts) AS last_tick,
    NOW() - MAX(ts) AS gap
FROM market_ticks
GROUP BY exchange_id
ORDER BY gap DESC;
"
```

Prometheus 알림:
```promql
# WS 재연결 빈도 (5분 내 3회 이상 = 장애)
increase(leviathan_ws_reconnect_total[5m]) > 3
```

### 1.2 원인 분류

| 원인 | 증상 | 대응 |
|------|------|------|
| 거래소 서버 점검 | 특정 거래소만 끊김 | Section 3으로 이동 |
| 네트워크 불안정 | 여러 거래소 동시 끊김 | 서버 네트워크 확인 |
| 엔진 메모리 부족 | OOM killer 발동 | Section 5로 이동 |
| 거래소 IP 차단 | reconnect 후 즉시 재단절 | IP 화이트리스트 확인 |
| 엔진 프로세스 크래시 | 로그 중단 | Docker 재시작 |

### 1.3 자동 복구 확인

엔진은 WS 끊김 시 **fast_backoff 패턴**으로 자동 재연결을 시도한다:

```bash
# 재연결 성공 여부 확인
docker compose logs leviathan-engine 2>/dev/null | \
  grep -E "ws_connected|collector_started|reconnect_success" | tail -20
```

자동 복구가 **2분 내** 이루어지면 추가 조치 불필요.

### 1.4 수동 복구 절차

자동 복구 실패 시:

**Step 1 — 엔진 컨테이너 재시작**

```bash
docker compose restart leviathan-engine
sleep 10
docker compose logs --tail=30 leviathan-engine | grep -E "collector|ws_connected|engine_ready"
```

**Step 2 — 특정 거래소 수집기만 재시작 (엔진 무중단)**

```bash
# 현재는 엔진 단일 프로세스 → 전체 재시작 필요
# 향후: 수집기 별도 프로세스 분리 시 개별 재시작 가능
docker compose restart leviathan-engine
```

**Step 3 — 재연결 후 데이터 수신 확인**

```bash
# 10개 거래소 모두 최근 1분 내 데이터 수신 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, COUNT(*) AS ticks_last_min
FROM market_ticks
WHERE ts > NOW() - INTERVAL '1 minute'
GROUP BY exchange_id
ORDER BY exchange_id;
"
# 기대: 10개 거래소 모두 > 0
```

### 1.5 장기 WS 불안정 대응

특정 거래소가 반복적으로 끊기는 경우:

```bash
# 해당 거래소를 비활성화 후 재배포
# engine/.env에서 TRADING_ACTIVE_EXCHANGES 수정
# 예: upbit 제거
TRADING_ACTIVE_EXCHANGES=["binance","bybit","okx","bitget","bithumb","coinone","binance_futures","okx_futures","bybit_futures"]
docker compose up -d leviathan-engine
```

---

## 2. DB 연결 실패

**Severity:** HIGH (데이터 소실 위험) / MEDIUM (엔진 in-memory 폴백으로 계속 동작)
**감지 신호:** `database_error` / `connection_pool_exhausted` 로그

### 2.1 감지 방법

```bash
# DB 연결 에러 확인
docker compose logs leviathan-engine 2>/dev/null | \
  grep -E "database_error|pool_exhausted|ConnectionRefused|asyncpg" | tail -30

# TimescaleDB 컨테이너 상태
docker compose ps leviathan-timescaledb
# Status: Up X hours (healthy) 이어야 함

# 직접 연결 테스트
docker compose exec timescaledb psql -U leviathan -d leviathan -c "SELECT 1;"
# 출력: 1 (연결 성공)
```

### 2.2 원인 분류 및 대응

**Case A — TimescaleDB 컨테이너 다운**

```bash
# 컨테이너 상태 확인
docker compose ps leviathan-timescaledb

# 재시작
docker compose restart leviathan-timescaledb
sleep 15  # 초기화 대기

# 헬스 확인
docker compose ps leviathan-timescaledb | grep healthy
```

**Case B — Connection Pool 고갈**

```bash
# 현재 연결 수 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT count(*), state
FROM pg_stat_activity
WHERE datname = 'leviathan'
GROUP BY state;
"

# idle in transaction 연결 강제 해제 (5분 초과)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'leviathan'
  AND state = 'idle in transaction'
  AND query_start < NOW() - INTERVAL '5 minutes';
"
```

**Case C — 디스크 공간 부족**

```bash
# DB 볼륨 사용량 확인
docker system df -v | grep leviathan

# TimescaleDB 내 테이블별 용량
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
    hypertable_name,
    pg_size_pretty(hypertable_size(hypertable_name::regclass)) AS size
FROM timescaledb_information.hypertables;
"

# market_ticks 오래된 데이터 수동 압축 (30일 보관 정책)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT compress_chunk(c) FROM show_chunks('market_ticks', older_than => INTERVAL '7 days') c;
"
```

**Case D — DATABASE_URL 설정 오류**

```bash
# 엔진 환경변수 확인
docker compose exec leviathan-engine env | grep DATABASE_URL
# 예상: postgresql+asyncpg://leviathan:leviathan@timescaledb:5432/leviathan
# 주의: Docker 내부에서는 hostname이 'timescaledb' (localhost 아님)
```

### 2.3 In-Memory 폴백 모드

DB 복구가 지연될 경우 엔진은 in-memory 폴백으로 계속 동작:

```bash
# 폴백 상태 확인 (로그에서 확인)
docker compose logs leviathan-engine | grep -E "db_fallback|in_memory_mode|db_unavailable"

# 폴백 시 제한 사항:
# - Walk-forward 분석 불가 → Live gate 평가 불가
# - 체결 기록 저장 안 됨 (재시작 시 PnL 초기화)
# - Shadow mode, Paper mode만 허용 (Live 금지)
```

### 2.4 DB 복구 후 엔진 재시작

```bash
# DB 복구 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT COUNT(*) FROM execution_log WHERE ts > NOW() - INTERVAL '1 hour';
"

# 엔진 재시작 (DB 재연결)
docker compose restart leviathan-engine
docker compose logs -f leviathan-engine | grep -E "db_connected|engine_ready"
```

**상세 절차는 Runbook 04 (Database Recovery)를 참조.**

---

## 3. 거래소 API 장애

**Severity:** HIGH
**감지 신호:** circuit_breaker OPEN, health_score < 0.95, HTTP 5xx 에러

### 3.1 감지 방법

```bash
# Circuit Breaker 상태 확인
docker compose logs leviathan-engine | \
  grep -E "circuit_breaker_open|health_score|api_error" | tail -20

# 거래소별 HTTP 에러율 (Prometheus)
# rate(leviathan_exchange_api_errors_total[5m]) by (exchange_id)
```

### 3.2 거래소별 장애 확인

```bash
# 10개 거래소 상태 페이지 (외부 확인)
# Binance:         https://www.binancezh.com/en/activity/systemUpdate
# Bybit:           https://announcements.bybit.com/
# OKX:             https://www.okx.com/support/hc/en-us
# Bitget:          https://www.bitget.com/support
# Upbit:           https://upbit.com/service_center/notice
# Bithumb:         https://www.bithumb.com/react/support/notice

# API 엔드포인트 직접 테스트
curl -s https://api.binance.com/api/v3/ping && echo "Binance: OK"
curl -s https://api.bybit.com/v5/market/time && echo "Bybit: OK"
curl -s https://www.okx.com/api/v5/public/time && echo "OKX: OK"
```

### 3.3 장애 거래소 비활성화

```bash
# engine/.env 수정 — 장애 거래소 제거
# 현재 전체: binance,bybit,okx,bitget,upbit,bithumb,coinone,binance_futures,okx_futures,bybit_futures

# 예: okx 장애 시 okx, okx_futures 동시 제거
vim engine/.env  # TRADING_ACTIVE_EXCHANGES 수정

# 엔진 재시작
docker compose up -d leviathan-engine
docker compose logs -f leviathan-engine | grep -E "exchange|active"
```

### 3.4 미체결 주문 확인

```bash
# 장애 발생 전 미체결 주문 여부 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, symbol, side, status, ts
FROM execution_log
WHERE exchange_id = 'okx'  -- 장애 거래소
  AND status = 'SUBMITTED'
  AND ts > NOW() - INTERVAL '30 minutes'
ORDER BY ts DESC;
"
# 결과 있으면 거래소 UI에서 수동 취소 또는 헤지 필요
# 상세: Runbook 02 (Exchange Outage) Section 3 참조
```

### 3.5 거래소 복구 후 재활성화

```bash
# 거래소 상태 확인 (HTTP 200 + 정상 응답)
curl -s https://api.binance.com/api/v3/time | python3 -c "import sys,json; print('OK:', json.load(sys.stdin))"

# engine/.env 복구 (비활성화한 거래소 추가)
vim engine/.env

# 엔진 재시작
docker compose up -d leviathan-engine
```

**상세 절차는 Runbook 02 (Exchange Outage)를 참조.**

---

## 4. Kill Switch 발동

**Severity:** CRITICAL
**감지 신호:** `kill_switch_tier1_complete`, `kill_switch_tier2_complete`, `halt_local` 로그
**즉시 조치:** 신규 주문 즉시 차단 (<1ms). 포지션 확인 후 복구 진행.

### 4.1 즉시 감지

```bash
# Kill Switch 발동 즉시 Telegram 알림 전송됨 (엔진 설정 시)
# 로그에서 직접 확인
docker compose logs leviathan-engine | \
  grep -E "kill_switch|halt_local|CRITICAL|is_halted" | tail -20

# API로 상태 확인
curl -s http://localhost:8000/mode
# {"halted": true, "reason": "..."}
```

### 4.2 3단계 분류

| 티어 | 트리거 | 로그 이벤트 | 대응 |
|------|--------|------------|------|
| **Tier 1** | 일일 손실 임계값 초과 | `daily_loss_exceeded` | 포지션 확인 + 원인 분석 |
| **Tier 2** | 기술적 장애 (CB > 30분, 레이턴시 급증) | `circuit_breaker_open` | 거래소/연결 복구 후 재개 |
| **Tier 3** | 수동 발동 | `halt_local` | 운영자 승인 후 재개 |

### 4.3 발동 후 즉시 조치 (10분 이내)

**Step 1 — 포지션 확인**

```bash
# API로 현재 포지션 확인
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d "username=${DASHBOARD_USER}&password=${DASHBOARD_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/positions | \
  python3 -c "import sys,json; [print(p) for p in json.load(sys.stdin)]"

# DB에서 미완료 주문 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT strategy_id, exchange_id, symbol, status, ts
FROM execution_log
WHERE status NOT IN ('SUCCESS', 'ROLLED_BACK')
  AND ts > NOW() - INTERVAL '2 hours'
ORDER BY ts DESC;
"
```

**Step 2 — Tier에 따른 대응**

```bash
# Tier 1 (손실): 손실 원인 파악
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT strategy_id, SUM(net_pnl) AS daily_pnl
FROM execution_log
WHERE ts > date_trunc('day', NOW())
GROUP BY strategy_id
ORDER BY daily_pnl ASC;
"

# Tier 2 (기술): 거래소/연결 복구 (Runbook 02 참조)
# Tier 3 (수동): 운영자 직접 승인 후 복구
```

### 4.4 Kill Switch 해제 절차

**전제조건:** 포지션 전량 확인 + 원인 해결 완료

```bash
# API로 Kill Switch 해제 (운영자 승인 필수)
curl -s -X POST http://localhost:8000/kill-switch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "clear", "reason": "operator_approved"}'

# 해제 확인
curl -s http://localhost:8000/mode | python3 -c "import sys,json; d=json.load(sys.stdin); print('Halted:', d.get('halted', '?'))"
# 기대: Halted: False
```

### 4.5 재개 전 체크리스트

```
[ ] is_halted() == False 확인
[ ] 포지션 전량 평탄화 (또는 의도적 보유 확인)
[ ] Live gate 6개 항목 전체 PASS
[ ] Telegram 알림 수신 정상
[ ] Tier 1: 일일 손실 원인 파악 + min_edge_bps 상향 조정
[ ] Tier 2: 거래소 health_score >= 0.95 모두 확인
[ ] 30분 Shadow 모드 재가동 후 문제 없으면 Live 전환
```

**상세 절차는 Runbook 01 (Kill Switch Recovery)를 참조.**

---

## 5. 메모리 누수 감지

**Severity:** MEDIUM (초기) → HIGH (OOM 임박)
**감지 신호:** RSS 메모리 선형 증가, OOM killer 로그, 응답 지연 증가

### 5.1 메모리 사용량 모니터링

```bash
# Docker 컨테이너 메모리 사용량 실시간 확인
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
# 예상: leviathan-engine < 2GB (초과 시 주의)

# 30분간 추이 확인 (선형 증가 = 누수 의심)
watch -n 60 'docker stats --no-stream leviathan-engine --format "{{.MemUsage}}"'
```

Prometheus 쿼리:
```promql
# 엔진 프로세스 RSS 메모리 (container_memory_rss)
container_memory_rss{name="leviathan-engine"}

# 메모리 증가율 (1시간)
increase(container_memory_rss{name="leviathan-engine"}[1h])
# 100MB/h 이상 증가 = 누수 의심
```

### 5.2 누수 원인 분류

```bash
# Python 메모리 사용 상세 (엔진 내부 로그)
docker compose logs leviathan-engine | grep -E "memory|heap|gc" | tail -20

# 대용량 오브젝트 추적 (엔진에 tracemalloc 활성화 시)
# ENGINE_TRACEMALLOC=true 환경변수 설정 후 재시작
```

| 원인 | 확인 방법 | 대응 |
|------|-----------|------|
| OrderBook 캐시 무제한 성장 | market_ticks 쿼리량 확인 | 캐시 TTL 설정 확인 |
| WebSocket 버퍼 누적 | ws_buffer_size 로그 확인 | WS 재연결 강제 |
| 거래 이력 메모리 누적 | execution_log DB 기록 지연 | DB 연결 확인 |
| asyncio Task 미해제 | loop.all_tasks() 카운트 | 엔진 재시작 |

### 5.3 즉각 완화 조치

```bash
# 1. Python GC 강제 실행 (응급, 효과 제한적)
docker compose exec leviathan-engine python3 -c "import gc; gc.collect(); print('GC done')"

# 2. 엔진 재시작 (가장 확실한 방법)
# - 먼저 포지션 확인 (Kill Switch 없이 재시작 시 미체결 주문 주의)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT COUNT(*) FROM execution_log WHERE status='SUBMITTED' AND ts > NOW() - INTERVAL '5 minutes';
"
# 0이면 안전하게 재시작
docker compose restart leviathan-engine
```

### 5.4 재발 방지

```bash
# Docker 메모리 제한 설정 (docker-compose.yml)
# deploy:
#   resources:
#     limits:
#       memory: 4G    # 4GB 초과 시 OOM kill (자동 재시작)
#     reservations:
#       memory: 512M

# OOM kill 발생 시 자동 재시작 정책 확인
docker inspect leviathan-engine | grep -A5 "RestartPolicy"
# "Name": "unless-stopped" 또는 "always" 권장
```

---

## 6. Docker 컨테이너 장애

**Severity:** HIGH (엔진/DB) / MEDIUM (모니터링/대시보드/로그)
**14개 컨테이너:** timescaledb, redis, engine, dashboard, nginx, grafana, prometheus, redis-exporter, auto-tuner, monitoring, db-backup, wal-backup, loki, promtail

### 6.1 전체 컨테이너 상태 확인

```bash
# 전체 상태 한눈에 보기
docker compose ps

# Restarting 상태 컨테이너 필터링
docker compose ps | grep -i restart

# 비정상 컨테이너 로그 확인
docker compose logs --tail=50 leviathan-nginx 2>/dev/null
docker compose logs --tail=50 leviathan-monitoring 2>/dev/null
```

### 6.2 컨테이너별 장애 대응

**leviathan-timescaledb 장애 (CRITICAL)**

```bash
docker compose restart leviathan-timescaledb
sleep 20
docker compose exec timescaledb psql -U leviathan -d leviathan -c "SELECT 1;"
# 복구 실패 시 Runbook 04 (Database Recovery) 참조
```

**leviathan-redis 장애**

```bash
docker compose restart leviathan-redis
# Redis 재시작 시 인메모리 캐시 초기화 (영구 데이터 없음)
docker compose exec redis redis-cli ping
# PONG
```

**leviathan-nginx 장애 (대시보드 접근 불가)**

```bash
docker compose logs leviathan-nginx | tail -30
docker compose restart leviathan-nginx
# nginx 설정 오류 시
docker compose exec nginx nginx -t  # 설정 테스트
```

**leviathan-auto-tuner / leviathan-monitoring 반복 재시작**

```bash
# 재시작 이유 확인
docker compose logs --tail=50 leviathan-auto-tuner
docker compose logs --tail=50 leviathan-monitoring

# DB 연결 의존성 (DB 먼저 확인)
docker compose ps leviathan-timescaledb

# 일시 중지 (엔진 운영에 필수 아님)
docker compose stop leviathan-auto-tuner leviathan-monitoring
# 복구 후 재개
docker compose start leviathan-auto-tuner leviathan-monitoring
```

**leviathan-loki / leviathan-promtail 장애 (로그 수집 중단)**

```bash
# Loki 준비 상태 확인
curl -s http://localhost:3100/ready && echo "Loki OK" || echo "Loki DOWN"

# 로그 확인
docker compose logs --tail=50 leviathan-loki
docker compose logs --tail=50 leviathan-promtail

# 재시작 (엔진 운영에 필수 아님 — 로그 수집만 중단)
docker compose restart leviathan-loki
sleep 10
docker compose restart leviathan-promtail

# loki_data 볼륨 용량 확인 (용량 부족 시 Loki 다운 가능)
docker system df -v | grep loki_data
```

**leviathan-db-backup / leviathan-wal-backup 실패**

```bash
# 백업 컨테이너는 restart: "no" — 실패 시 Exited(1) 상태
docker compose ps leviathan-db-backup leviathan-wal-backup

# 실패 원인 확인
docker compose logs leviathan-db-backup
docker compose logs leviathan-wal-backup

# 수동 재실행 (실패 원인 해결 후)
docker compose run --rm leviathan-db-backup
docker compose run --rm leviathan-wal-backup

# 백업 파일 존재 확인
DUMP_PATH=$(docker volume inspect leviathan_db_backups --format '{{.Mountpoint}}' 2>/dev/null)
[ -n "$DUMP_PATH" ] && ls -lht "${DUMP_PATH}"/*.dump 2>/dev/null | head -5
```

**leviathan-engine 장애 (CRITICAL)**

```bash
# 1. 포지션 확인
curl -s http://localhost:8000/positions || echo "Engine down"

# 2. 재시작
docker compose restart leviathan-engine

# 3. 시작 확인 (30초 내 ready)
timeout 60 bash -c 'until docker compose logs --tail=5 leviathan-engine 2>/dev/null | grep -q "engine_ready"; do sleep 3; done && echo "Engine READY"'
```

### 6.3 전체 스택 재시작

```bash
# 순서: 데이터 계층 → 엔진 → 프론트엔드 → 로그/모니터링/운영
docker compose stop

# 1단계: 데이터 계층 (필수 의존성)
docker compose start leviathan-timescaledb leviathan-redis
sleep 20

# 2단계: 엔진 + 대시보드
docker compose start leviathan-engine
sleep 10
docker compose start leviathan-dashboard

# 3단계: 프론트엔드 프록시
docker compose start leviathan-nginx

# 4단계: 로그 수집 파이프라인 (loki 먼저, promtail 나중)
docker compose start leviathan-loki
sleep 10
docker compose start leviathan-promtail

# 5단계: 모니터링 스택
docker compose start leviathan-prometheus leviathan-grafana leviathan-redis-exporter

# 6단계: 운영 서비스
docker compose start leviathan-monitoring leviathan-auto-tuner

# 백업 컨테이너는 restart:"no" → 수동 실행 시에만
# docker compose run --rm leviathan-db-backup
# docker compose run --rm leviathan-wal-backup

# 전체 상태 확인 (db-backup, wal-backup은 Exited(0) 정상)
docker compose ps
```

---

## 7. 대시보드 / API 응답 없음

**Severity:** MEDIUM
**감지 신호:** HTTP 502/503, WebSocket 연결 실패, 로그인 불가

### 7.1 진단

```bash
# API 서버 직접 확인 (포트 8000)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
# 200 이어야 함

# 대시보드 확인 (포트 3000)
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# 200 이어야 함

# Nginx를 통한 접근 확인
curl -s -o /dev/null -w "%{http_code}" https://localhost
# 200 이어야 함 (TLS 인증서 자체 서명 시 -k 추가)
```

### 7.2 JWT 인증 장애

```bash
# JWT 토큰 발급 테스트
curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=test"
# {"access_token": "...", "token_type": "bearer"} 이어야 함

# JWT 설정 확인
docker compose exec leviathan-engine env | grep -E "JWT_SECRET|DASHBOARD_USER|DASHBOARD_PASSWORD"
```

### 7.3 WebSocket 피드 장애

```bash
# 대시보드 WebSocket 피드 (엔진 포트 8000 /ws)
# 로그에서 WS 연결 확인
docker compose logs leviathan-engine | grep -E "ws_client|dashboard_feed|websocket" | tail -20
```

### 7.4 포트폴리오 API 확인 (US-072)

```bash
# /portfolio-summary 엔드포인트 확인
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d "username=${DASHBOARD_USER}&password=${DASHBOARD_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/portfolio-summary | \
  python3 -m json.tool
# 거래소별 잔고 + 총자산 반환
```

---

## 8. 장애 대응 공통 절차

### 8.1 Telegram 알림 전송

```bash
# 장애 인지 즉시 운영 채널 알림
python3 -c "
import asyncio, os
import sys
sys.path.insert(0, 'engine')
from src.infra.telegram import TelegramNotifier

async def alert(msg):
    n = TelegramNotifier.from_env()
    await n.send(msg)

asyncio.run(alert('INCIDENT: [장애유형] 감지. 대응 시작. 예상 복구 시간: [ETA]'))
"
```

### 8.2 장애 기록 (의무)

```bash
# 장애 로그 파일 생성
INCIDENT_FILE="incidents/$(date +%Y%m%d_%H%M%S)_incident.md"
mkdir -p incidents
cat > $INCIDENT_FILE << 'EOF'
# 장애 기록

- **발생 시각:** YYYY-MM-DD HH:MM KST
- **감지 방법:** [Telegram 알림 / 모니터링 / 수동 발견]
- **장애 유형:** [WS끊김 / DB장애 / 거래소장애 / KillSwitch / 메모리누수]
- **영향 범위:** [전체 / 특정 거래소 / 특정 전략]
- **대응 시작:** YYYY-MM-DD HH:MM KST
- **복구 완료:** YYYY-MM-DD HH:MM KST
- **원인:** [원인 기술]
- **대응 내용:** [조치 내용]
- **재발 방지:** [예방 조치]
EOF
echo "장애 기록: $INCIDENT_FILE"
```

### 8.3 전체 시스템 상태 빠른 확인 (Quick Health Check)

```bash
#!/bin/bash
# 전체 시스템 상태 30초 점검 (14개 컨테이너)

echo "=== Docker Containers (14) ==="
docker compose ps --format "table {{.Name}}\t{{.Status}}"
# 정상: engine/timescaledb/redis/dashboard/nginx/grafana/prometheus/
#       redis-exporter/monitoring/auto-tuner/loki/promtail = Up (healthy)
# 정상: db-backup/wal-backup = Exited (0) [restart:"no"]

echo ""
echo "=== Engine API ==="
curl -s -o /dev/null -w "Health: %{http_code}\n" http://localhost:8000/health
curl -s -o /dev/null -w "Mode:   %{http_code}\n" http://localhost:8000/mode

echo ""
echo "=== DB Last Tick ==="
docker compose exec -T timescaledb psql -U leviathan -d leviathan -c \
  "SELECT exchange_id, NOW()-MAX(ts) AS gap FROM market_ticks GROUP BY exchange_id ORDER BY gap DESC;" \
  2>/dev/null

echo ""
echo "=== Redis ==="
docker compose exec -T redis redis-cli ping

echo ""
echo "=== Loki Status ==="
curl -s http://localhost:3100/ready && echo " (ready)" || echo "Loki DOWN"

echo ""
echo "=== Recent Engine Errors ==="
docker compose logs --tail=20 leviathan-engine 2>/dev/null | grep -iE "error|critical|kill_switch|circuit_breaker"

echo ""
echo "=== Loki Recent Errors (logcli 설치 시) ==="
logcli query '{container="leviathan-engine"} |= "error"' \
  --addr=http://localhost:3100 --since=5m --limit=10 2>/dev/null || true
```

---

## 9. 운영 Runbook 요약 (Quick Reference)

### 9.1 전체 Runbook 목록

| # | 파일 | 주제 | Severity | SLA |
|---|------|------|----------|-----|
| 01 | `01_kill_switch_recovery.md` | Kill Switch 복구 | CRITICAL | 확인 5분, 포지션 15분, 재개 결정 60분 |
| 02 | `02_exchange_outage.md` | 거래소 장애 대응 | HIGH | 감지 2분, 페일오버 10분, 복구 2시간 |
| 03 | `03_drawdown_breach.md` | Drawdown 초과 대응 | HIGH | 감지 5분, 파라미터 조정 30분, WFA 24시간 |
| 04 | `04_database_recovery.md` | DB 복구 | HIGH | 감지 5분, 폴백 10분, 복구 4시간 |
| 05 | `05_deployment.md` | 배포 / 롤백 | MEDIUM/HIGH | 배포 30분 윈도우, 롤백 10분 |
| 06 | `06_security_api_keys.md` | 보안 / API 키 관리 | CRITICAL/HIGH | 침해 감지 5분, 교체 30분 |
| 07 | `07_incident_response.md` | 통합 장애 대응 (본 문서) | 유형별 상이 | 감지 2분, 초기 대응 10분 |

### 9.2 에스컬레이션 경로

```
Level 0 (자동): 엔진 자가 복구
  - WS 재연결 (fast_backoff, 2분 내 자동)
  - Circuit Breaker 자동 HALF_OPEN (300초 cooldown)
  - Docker restart unless-stopped (컨테이너 자동 재시작)

Level 1 (운영자 단독 대응): 10분 이내
  - Docker 컨테이너 재시작
  - 장애 거래소 비활성화 (.env 수정 + 재배포)
  - DB 연결 풀 고갈 해소

Level 2 (심화 대응): 30분~1시간
  - Kill Switch 해제 절차 (Runbook 01)
  - DB 복구 절차 (Runbook 04)
  - API 키 교체 (Runbook 06 Section 3)
  - 파라미터 조정 후 Walk-Forward 재평가 (Runbook 03)

Level 3 (전체 재배포): 1~4시간
  - 롤백 절차 (Runbook 05 Section 4)
  - DB WAL 복구 (Runbook 04 Section 3)
  - 침해 대응 (Runbook 06 Section 5)

Level 4 (비상): 즉시 에스컬레이션
  - ROLLBACK_FAILED (stranded position)
  - 키 침해 + 비정상 거래 발생
  - DB 완전 손실
```

### 9.3 Telegram 알림 채널

| 채널 | 용도 | 트리거 |
|------|------|--------|
| `@leviathan_ops` | 운영 알림 (기본 채널) | 엔진 시작/중단, 전략 PnL 요약, 파라미터 변경 |
| `@leviathan_alerts` | 장애 알림 | Kill Switch 발동, Circuit Breaker OPEN, Drawdown 초과 |
| `@leviathan_security` | 보안 알림 | API 키 교체, 침해 의심, 인증 오류 급증 |

Telegram 알림 테스트:

```bash
python3 -c "
import asyncio, os, sys
sys.path.insert(0, 'engine')
from src.infra.telegram import TelegramNotifier

async def test():
    n = TelegramNotifier.from_env()
    await n.send('RUNBOOK TEST: Telegram 알림 정상 동작 확인 - $(date)')

asyncio.run(test())
"
```

환경변수 확인:

```bash
docker compose exec leviathan-engine env | grep -E "TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID"
# TELEGRAM_BOT_TOKEN=123456789:AAF...
# TELEGRAM_CHAT_ID=-100123456789
```

### 9.4 Loki 로그 쿼리 빠른 참조

```bash
# logcli 설치 (필요 시)
# https://grafana.com/docs/loki/latest/tools/logcli/

LOKI_ADDR="--addr=http://localhost:3100"

# 엔진 에러 (최근 1시간)
logcli query '{container="leviathan-engine"} |= "error"' $LOKI_ADDR --since=1h

# Kill Switch 이벤트
logcli query '{container="leviathan-engine"} |= "kill_switch"' $LOKI_ADDR --since=24h

# Circuit Breaker 발동
logcli query '{container="leviathan-engine"} |= "circuit_breaker_open"' $LOKI_ADDR --since=24h

# WS 재연결
logcli query '{container="leviathan-engine"} |= "reconnect_attempt"' $LOKI_ADDR --since=1h

# DB 에러
logcli query '{container="leviathan-engine"} |= "database_error"' $LOKI_ADDR --since=1h

# Nginx 접근 로그 (4xx/5xx)
logcli query '{container="leviathan-nginx"} |= "\" 5"' $LOKI_ADDR --since=1h

# Grafana에서 Loki 탐색: http://localhost:3001 → Explore → Loki 데이터소스
```

---

## References

- Kill Switch: `engine/docs/runbooks/01_kill_switch_recovery.md`
- Exchange Outage: `engine/docs/runbooks/02_exchange_outage.md`
- Database Recovery: `engine/docs/runbooks/04_database_recovery.md`
- Deployment: `engine/docs/runbooks/05_deployment.md`
- Security: `engine/docs/runbooks/06_security_api_keys.md`
- Kill switch 구현: `engine/src/risk/kill_switch.py`
- Circuit breaker: `engine/src/risk/circuit_breaker.py`
- WS 수집기: `engine/src/collectors/` (10개 거래소)
- API 라우터: `engine/src/api/routes/`
- Loki 설정: `infra/loki/loki-config.yaml`
- Promtail 설정: `infra/promtail/promtail-config.yaml`
- WAL 백업 스크립트: `infra/backup/wal-backup.sh`
- DB 백업 스크립트: `scripts/backup_db.sh`
- QUANT_MANIFESTO.md Section 7 (Risk Controls)
