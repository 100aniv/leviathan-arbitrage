# LEVIATHAN 운영 가이드

> **대상**: 운영자, SRE, 시스템 관리자
> **버전**: v1.0 (2026-03-11)
> **아키텍처**: Python 3.12+ AsyncIO + Rust (PyO3) | Docker Compose 8 컨테이너
> **관련 문서**: `SSOT.md` (설계 원본) | `docs/quick_start_ko.md` (초기 설치)

---

## 목차

1. [시스템 시작 절차](#1-시스템-시작-절차)
2. [시스템 중지 절차](#2-시스템-중지-절차)
3. [모니터링](#3-모니터링)
4. [장애 대응 체크리스트](#4-장애-대응-체크리스트)
5. [환경 변수 설정 가이드](#5-환경-변수-설정-가이드)
6. [일상 운영 작업](#6-일상-운영-작업)
7. [백업 및 복구](#7-백업-및-복구)

---

## 1. 시스템 시작 절차

### 1.1 사전 확인

```bash
# 1. 저장소 최신화
cd /path/to/arbitrage_OMC
git status && git pull

# 2. 환경 변수 파일 확인 (두 파일 반드시 존재해야 함)
ls -la .env engine/.env

# 3. 필수 포트 사용 여부 확인
lsof -i :8000 -i :8001 -i :5432 -i :6379 -i :3000 -i :3001 2>/dev/null
```

### 1.2 인프라 (Docker) 시작

```bash
# 전체 스택 시작
docker compose up -d

# 시작 상태 확인 (모든 컨테이너 healthy 확인)
docker compose ps

# 예상 출력:
# NAME                      STATUS          PORTS
# leviathan-engine          healthy         0.0.0.0:8000->8000/tcp, 0.0.0.0:8001->8001/tcp
# leviathan-dashboard       healthy         0.0.0.0:3000->3000/tcp
# leviathan-timescaledb     healthy         0.0.0.0:5432->5432/tcp
# leviathan-redis           healthy         0.0.0.0:6379->6379/tcp
# leviathan-prometheus      healthy         0.0.0.0:9090->9090/tcp
# leviathan-grafana         healthy         0.0.0.0:3001->3001/tcp
# leviathan-nginx           healthy         0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
# leviathan-redis-exporter  healthy
```

> **주의**: unhealthy 컨테이너가 있으면 [§4 장애 대응](#4-장애-대응-체크리스트) 참조

### 1.3 엔진 헬스 체크

```bash
# REST API 상태 확인
curl -s http://localhost:8000/health | python3 -m json.tool

# 정상 응답:
# {"status": "healthy", "mode": "shadow", "uptime_seconds": 42}

# Prometheus 메트릭 수집 확인
curl -s http://localhost:8000/metrics | grep leviathan_signals_total
```

### 1.4 모드별 시작 확인

| 모드 | DATA_MODE | EXECUTION_MODE | 확인 명령 |
|------|-----------|----------------|-----------|
| Shadow (기본) | `shadow` | `paper` | `curl http://localhost:8000/api/v1/shadow/stats` |
| Paper | `real_public` | `paper` | `curl http://localhost:8000/health` |
| Backtest | `synthetic` | `paper` | 별도 CLI 사용 |
| Live | `shadow` | `live` | LiveGate 6-check 통과 후만 허용 |

### 1.5 로컬 개발 (Docker 없이) 실행

```bash
# TimescaleDB + Redis는 Docker로 유지하면서 엔진만 로컬 실행
cd engine
python -m src.main

# 또는 Shadow 10분 테스트
timeout 600 python -m src.main
```

---

## 2. 시스템 중지 절차

### 2.1 정상 중지 (Graceful Shutdown)

```bash
# 1. 엔진에 SIGTERM 전송 (graceful shutdown, ~30초 소요)
docker compose stop engine

# 2. 전체 스택 중지 (데이터 보존)
docker compose stop

# 3. 볼륨 삭제 없이 컨테이너 제거
docker compose down
```

> **주의**: `docker compose down -v`는 TimescaleDB/Redis 볼륨을 삭제합니다. 절대 사용 금지.

### 2.2 긴급 중지

```bash
# Kill Switch 활성화 (거래 즉시 중단, 인프라 유지)
curl -X POST http://localhost:8000/risk/kill-switch/activate \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "operator_manual_halt"}'

# 확인
curl http://localhost:8000/risk/metrics | python3 -m json.tool
# kill_switch_active: true 확인
```

### 2.3 완전 재시작

```bash
# 컨테이너 재시작 (볼륨 유지)
docker compose restart

# 특정 서비스만 재시작
docker compose restart engine
docker compose restart dashboard
```

---

## 3. 모니터링

### 3.1 대시보드 접속

| 서비스 | URL | 인증 |
|--------|-----|------|
| LEVIATHAN 대시보드 | `http://localhost:3000` | .env `DASHBOARD_USER` / `DASHBOARD_PASSWORD` |
| Grafana | `http://localhost:3001` | .env `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |
| Prometheus | `http://localhost:9090` | 없음 (내부망 전용) |
| Engine API | `http://localhost:8000/docs` | JWT 토큰 |

### 3.2 핵심 메트릭 모니터링

**Shadow 실시간 메트릭** (1초 업데이트):
```bash
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/shadow/stats | python3 -m json.tool
```

```json
{
  "total_pnl": 39733.58,
  "win_rate": 0.965,
  "total_trades": 2325,
  "max_drawdown": 0.042,
  "by_strategy": [
    {"strategy_id": "cross_exchange", "pnl": 25000.0, "trades": 1155, "win_rate": 0.97}
  ]
}
```

**포트폴리오 잔고**:
```bash
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary | python3 -m json.tool
```

**리스크 메트릭**:
```bash
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/risk/metrics | python3 -m json.tool
```

### 3.3 LiveGate 6-check 상태

| 체크 | 임계값 | 모니터링 경로 |
|------|--------|---------------|
| Sharpe (7일 롤링) | >= 2.5 | Grafana > LEVIATHAN > Sharpe Ratio |
| Max Drawdown | < 5% | `/risk/metrics` → `max_drawdown` |
| 일일 신호 수 | >= 100/day | Prometheus → `leviathan_signals_total` |
| Kill Switch | Not halted | `/risk/metrics` → `kill_switch_active` |
| Circuit Breaker | CLOSED | `/risk/metrics` → `circuit_breaker_state` |
| 거래소 Health | >= 95% | `/exchanges` → `health_pct` |

### 3.4 로그 모니터링

```bash
# 엔진 실시간 로그 (마지막 100줄 + follow)
docker compose logs -f --tail=100 engine

# 에러만 필터링
docker compose logs engine 2>&1 | grep -E "ERROR|CRITICAL|Traceback"

# 특정 시간대 로그 (1시간 전부터)
docker compose logs --since=1h engine

# TimescaleDB 로그
docker compose logs timescaledb

# 전체 스택 로그
docker compose logs -f --tail=50
```

### 3.5 Grafana 주요 대시보드

1. **LEVIATHAN Overview**: PnL, 승률, 거래량, Sharpe 추세
2. **Exchange Health**: 10개 거래소별 연결 상태, 레이턴시
3. **Risk Monitor**: Kill Switch 이력, Circuit Breaker 상태, MDD 추세
4. **Infrastructure**: CPU/메모리/디스크, Redis 큐 깊이, DB 연결 수

### 3.6 Telegram 알림

엔진은 다음 이벤트 시 Telegram으로 알림 전송:
- Shadow PnL 일일 요약 (00:00 UTC)
- Kill Switch 활성화
- Circuit Breaker 개방
- 거래소 연결 장애 (3회 재시도 후)
- 거래 오류 (CRITICAL 레벨)

```bash
# Telegram 알림 테스트
curl -X POST http://localhost:8000/debug/test-alert \
  -H "Authorization: Bearer $JWT_TOKEN"
```

---

## 4. 장애 대응 체크리스트

### 4.1 컨테이너 unhealthy

```bash
# 1. 어떤 컨테이너가 문제인지 확인
docker compose ps

# 2. 문제 컨테이너 로그 확인
docker compose logs --tail=200 <service_name>

# 3. 컨테이너 재시작 시도
docker compose restart <service_name>

# 4. 재시작 후 30초 대기 후 상태 재확인
sleep 30 && docker compose ps
```

### 4.2 엔진 응답 없음

```bash
# 1. 헬스체크
curl -m 5 http://localhost:8000/health || echo "TIMEOUT"

# 2. 프로세스 확인
docker compose exec engine ps aux | grep python

# 3. Kill Switch 확인 (거래 중단 여부)
curl -s http://localhost:8000/risk/metrics | python3 -c "import sys,json; d=json.load(sys.stdin); print('KS:', d.get('kill_switch_active'))"

# 4. 엔진 재시작
docker compose restart engine

# 5. 재시작 후 shadow stats 확인
sleep 30 && curl -s http://localhost:8000/api/v1/shadow/stats
```

### 4.3 거래소 연결 장애

```bash
# 모든 거래소 연결 상태 확인
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/exchanges | python3 -m json.tool

# 장애 거래소 WebSocket 재연결 (자동, 최대 5회 재시도)
# 자동 재연결이 실패하면 로그에서 확인:
docker compose logs engine | grep -E "reconnect|disconnect|<exchange_name>"
```

### 4.4 DB 연결 장애

```bash
# TimescaleDB 상태 확인
docker compose ps timescaledb
docker compose logs timescaledb --tail=50

# 직접 접속 테스트
docker compose exec timescaledb psql -U leviathan -d leviathan -c "SELECT NOW();"

# 연결 수 확인 (최대 100)
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='leviathan';"

# DB 재시작 (마지막 수단)
docker compose restart timescaledb
```

### 4.5 메모리/CPU 과부하

```bash
# 컨테이너별 리소스 사용량
docker stats --no-stream

# 엔진 메모리 한도: 2GB
# 초과 시 자동 재시작됨 (restart: unless-stopped)

# 수동으로 특정 컨테이너 리소스 확인
docker stats leviathan-engine --no-stream
```

### 4.6 Kill Switch 해제

```bash
# Kill Switch 상태 확인
curl -s http://localhost:8000/risk/metrics | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('KillSwitch:', d['kill_switch_active'])"

# 해제 (운영자 승인 후)
curl -X POST http://localhost:8000/risk/kill-switch/deactivate \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "operator_cleared_after_review"}'

# 해제 후 Shadow stats 재확인
sleep 5 && curl -s http://localhost:8000/api/v1/shadow/stats
```

---

## 5. 환경 변수 설정 가이드

> **중요**: `.env` (Docker용)와 `engine/.env` (로컬 엔진용) **두 파일 모두 동기화** 필수.

### 5.1 필수 환경 변수

```bash
# engine/.env (또는 루트 .env)

# --- 실행 모드 ---
ENGINE_ENV=dev                  # dev | staging | prod | test (development 사용 금지!)
EXECUTION_MODE=paper            # paper | live (live는 LiveGate 통과 후만)
DATA_MODE=shadow                # synthetic | real_public | shadow

# --- DB 연결 ---
DATABASE_URL=postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan
REDIS_URL=redis://localhost:6379/0

# --- 리스크 파라미터 ---
MAX_POSITION_USD=1000           # 거래당 최대 포지션 (USD)
MAX_DAILY_LOSS_USD=500          # 일일 최대 손실 (Kill Switch 트리거)
MIN_EDGE_BPS=3                  # 최소 수익 엣지 (basis points)
KILL_SWITCH_ENABLED=true

# --- 보안 ---
JWT_SECRET=<32자 이상 랜덤 문자열>
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<강력한 패스워드>
```

### 5.2 거래소 API 키 설정

```bash
# Binance
BINANCE_API_KEY=<your_key>
BINANCE_API_SECRET=<your_secret>
BINANCE_TESTNET=false           # 운영: false, 테스트: true

# OKX
OKX_API_KEY=<your_key>
OKX_API_SECRET=<your_secret>
OKX_PASSPHRASE=<your_passphrase>
OKX_TESTNET=false

# Bybit
BYBIT_API_KEY=<your_key>
BYBIT_API_SECRET=<your_secret>
BYBIT_TESTNET=false

# 국내 거래소 (KRW 페어)
UPBIT_ACCESS_KEY=<your_key>
UPBIT_SECRET_KEY=<your_secret>
BITHUMB_API_KEY=<your_key>
BITHUMB_API_SECRET=<your_secret>
COINONE_ACCESS_TOKEN=<your_key>
COINONE_SECRET_KEY=<your_secret>
```

### 5.3 알림 설정

```bash
# Telegram
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>

# Grafana 관리자
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=<강력한_패스워드>
```

### 5.4 Docker 환경 변수 오버라이드

Docker Compose 환경에서는 서비스 간 통신에 Docker 서비스명 사용:

```yaml
# docker-compose.yml이 자동으로 오버라이드:
DB_HOST: timescaledb      # (localhost → timescaledb)
REDIS_HOST: redis          # (localhost → redis)
```

---

## 6. 일상 운영 작업

### 6.1 일일 체크리스트

```bash
#!/bin/bash
# 매일 09:00 UTC 실행 권장

echo "=== LEVIATHAN Daily Check ==="
echo "1. 컨테이너 상태:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo "2. Shadow 메트릭:"
curl -s http://localhost:8000/api/v1/shadow/stats 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'PnL: {d[\"total_pnl\"]:.2f} | WR: {d[\"win_rate\"]*100:.1f}% | Trades: {d[\"total_trades\"]} | MDD: {d[\"max_drawdown\"]*100:.2f}%')"

echo "3. 리스크 상태:"
curl -s http://localhost:8000/risk/metrics 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'KillSwitch: {d.get(\"kill_switch_active\")} | CircuitBreaker: {d.get(\"circuit_breaker_state\")}')"

echo "4. 최근 에러:"
docker compose logs engine --since=24h 2>&1 | grep -c "ERROR\|CRITICAL"
```

### 6.2 테스트 실행

```bash
# 전체 단위 테스트
cd engine && python -m pytest tests/ -x --tb=short

# 빠른 회귀 테스트 (핵심 모듈만)
cd engine && python -m pytest tests/unit/ -x --tb=short -q

# Shadow 10분 테스트
cd engine && timeout 600 python -m src.main
```

### 6.3 파라미터 업데이트

```bash
# 전략 파라미터 수정
vim engine/config/strategy_params.json

# 리스크 파라미터 수정 (재시작 필요)
vim engine/.env
docker compose restart engine
```

### 6.4 DB 유지보수

```bash
# TimescaleDB 압축 통계 확인
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT * FROM timescaledb_information.chunks ORDER BY range_end DESC LIMIT 5;"

# 오래된 데이터 정리 (90일 이상)
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT drop_chunks('trades', INTERVAL '90 days');"

# 디스크 사용량
docker compose exec timescaledb psql -U leviathan -d leviathan \
  -c "SELECT pg_size_pretty(pg_database_size('leviathan'));"
```

---

## 7. 백업 및 복구

### 7.1 DB 수동 백업

```bash
# TimescaleDB 전체 백업
docker compose exec timescaledb pg_dump \
  -U leviathan -d leviathan \
  -Fc -f /backups/leviathan_$(date +%Y%m%d_%H%M%S).dump

# 백업 파일 호스트로 복사
docker cp leviathan-timescaledb:/backups/. ./db_backups/
```

### 7.2 DB 복구

```bash
# 백업에서 복구
docker compose exec timescaledb pg_restore \
  -U leviathan -d leviathan \
  -Fc /backups/leviathan_20260101_000000.dump

# 자세한 복구 절차: docs/runbooks/server-crash-recovery.md 참조
```

### 7.3 자동 백업 확인

Docker Compose의 `db-backup` 서비스가 매일 자동 백업 실행 (7일 보관):
```bash
# 백업 파일 목록 확인
docker run --rm -v leviathan_db_backups:/backups alpine ls -lh /backups/
```

---

## 관련 문서

- `SSOT.md` — 유일한 설계 문서 (아키텍처, 전략, 상태)
- `docs/runbooks/server-crash-recovery.md` — 서버 크래시 복구
- `docs/runbooks/position-recovery.md` — 포지션 불일치 복구
- `docs/quick_start_ko.md` — 초기 설치 가이드
- `QUANT_MANIFESTO.md` — 수학적 기반 및 공식

---

*최종 수정: 2026-03-11 | US-057 운영 가이드*
