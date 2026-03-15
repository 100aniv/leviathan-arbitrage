# LEVIATHAN 모니터링 가이드

> Shadow 테스트 또는 Live 운영 중 시스템 상태를 확인하는 방법을 안내합니다.

---

## 1. 웹 대시보드 (Dashboard)

**접속**: http://localhost:3000
**로그인**: admin / leviathan (또는 `.env`의 `DASHBOARD_USER` / `DASHBOARD_PASSWORD`)

### 페이지별 확인 사항

| 페이지 | URL | 확인 내용 |
|--------|-----|----------|
| **Overview** | `/` | 실시간 PnL, 승률(WR), 활성 거래 수, 포트폴리오 요약, 리스크 게이지 |
| **Strategies** | `/strategies` | 전략별 성과 (cross_exchange, triangular, funding_rate 등), 개별 PnL/WR |
| **Portfolio** | `/portfolio` | 거래소별 잔고 (가상), Equity Curve, Sharpe/MDD/Calmar 지표 |
| **System** | `/system` | 엔진 상태, 거래소 연결 상태, Redis/DB 헬스 |

### 실시간 데이터
- WebSocket으로 1초마다 자동 갱신 (`/ws/feed`)
- 별도 새로고침 불필요

---

## 2. Grafana 대시보드

**접속**: http://localhost:3001
**로그인**: admin / leviathan (또는 `.env`의 `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`)

### 주요 대시보드

| 대시보드 | 확인 내용 |
|---------|----------|
| **Engine Performance** | 전략별 PnL 추이, 거래 빈도, 신호 생성 수 |
| **Exchange Latency** | 거래소별 WebSocket 지연시간, 연결 상태 |
| **System Resources** | CPU/메모리/네트워크 사용량, 컨테이너 상태 |
| **Alerts** | 설정된 알림 규칙, 발생 이력 |

### Prometheus 직접 접근
- **접속**: http://localhost:9090
- 유용한 쿼리:
  - `leviathan_pnl_total` — 누적 PnL
  - `leviathan_trades_total` — 총 거래 수
  - `leviathan_active_connections` — 활성 WebSocket 연결 수

---

## 3. Engine API (REST)

**기본 URL**: http://localhost:8000

### 인증 없이 접근 가능

| 엔드포인트 | 설명 | 사용법 |
|-----------|------|--------|
| `GET /health` | 엔진 헬스 체크 | `curl localhost:8000/health` |
| `GET /metrics` | Prometheus 메트릭 | `curl localhost:8000/metrics` |

### 인증 필요 (JWT)

먼저 토큰 획득:
```bash
# 로그인하여 JWT 토큰 받기
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"leviathan"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo $TOKEN
```

주요 API:
```bash
# Shadow 통계 (PnL, 승률, 거래수, MDD)
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/shadow/stats | python3 -m json.tool

# 최근 거래 목록
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/shadow/recent-trades | python3 -m json.tool

# 포트폴리오 요약
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/portfolio-summary | python3 -m json.tool

# 시스템 상태
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/system/health | python3 -m json.tool

# 거래소 상태
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/exchange/status | python3 -m json.tool
```

---

## 4. CLI 모니터링 명령어

### 실시간 로그 보기
```bash
# 엔진 로그 실시간 추적
docker compose logs -f engine

# 특정 키워드 필터링
docker compose logs -f engine | grep -i "trade\|pnl\|error\|warning"

# 전략 신호만 보기
docker compose logs -f engine | grep "signal\|strategy"
```

### 주기적 상태 확인
```bash
# 1분마다 Shadow 통계 자동 조회
watch -n 60 'curl -s -H "Authorization: Bearer YOUR_TOKEN" localhost:8000/api/v1/shadow/stats | python3 -m json.tool'

# Docker 컨테이너 상태
watch -n 30 'docker compose ps'

# Redis 상태 확인
docker compose exec redis redis-cli -a leviathan-redis-secret INFO server | head -20
```

### 빠른 상태 확인 스크립트
```bash
#!/bin/bash
# leviathan-status.sh — 한눈에 상태 확인

echo "=== Docker 상태 ==="
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo ""
echo "=== 엔진 헬스 ==="
curl -s localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "엔진 미기동"

echo ""
echo "=== Shadow 통계 ==="
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"leviathan"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

if [ -n "$TOKEN" ]; then
  curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/shadow/stats | python3 -m json.tool 2>/dev/null
else
  echo "토큰 획득 실패"
fi
```

---

## 5. Telegram 알림

### 거래 알림 (자동)
- 체결 발생 시 실시간 알림
- PnL 변동 알림
- KillSwitch 발동 알림
- 설정: `.env`의 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`

### 워크플로우 알림 (자동)
- Phase 완료/실패 알림
- 에스컬레이션 알림
- 컨텍스트 경고 알림
- 설정: `.env`의 `WORKFLOW_TELEGRAM_BOT_TOKEN` + `WORKFLOW_TELEGRAM_CHAT_ID`

### Telegram 설정법
1. [@BotFather](https://t.me/BotFather)에서 봇 생성
2. 봇 토큰을 `.env`에 입력
3. 봇에게 메시지 전송 후 Chat ID 확인: `curl https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Chat ID를 `.env`에 입력
5. `TELEGRAM_ENABLED=true` 설정

---

## 6. 문제 발생 시 확인 순서

### Shadow 테스트 중 이상 감지 시

1. **즉시 확인**: `curl localhost:8000/health` — 엔진 기동 중인지
2. **로그 확인**: `docker compose logs --tail=50 engine` — 최근 에러
3. **거래소 상태**: Dashboard > System 페이지 — 연결 끊긴 거래소
4. **PnL 확인**: Dashboard > Overview — 급격한 손실 여부
5. **KillSwitch**: 로그에서 `HALT` 키워드 검색

### 엔진이 멈춘 경우

```bash
# 컨테이너 상태 확인
docker compose ps

# 재시작
docker compose restart engine

# 전체 재시작
docker compose down && docker compose up -d
```

### 데이터가 안 보이는 경우

```bash
# TimescaleDB 확인
docker compose exec timescaledb pg_isready -U leviathan

# Redis 확인
docker compose exec redis redis-cli -a leviathan-redis-secret PING

# 로그에서 DB 에러 확인
docker compose logs engine | grep -i "database\|redis\|connection"
```

---

## 7. 핵심 지표 해석

| 지표 | 정상 범위 | 주의 | 위험 |
|------|----------|------|------|
| **PnL** | 양수 유지 | 0 근처 횡보 | 지속 음수 |
| **승률 (WR)** | >60% | 50-60% | <50% |
| **MDD** | <2% | 2-5% | >5% |
| **거래소 연결** | 10/10 | 8-9/10 | <8/10 |
| **거래 빈도** | >10/h | 5-10/h | <5/h 또는 0 |
| **메모리** | <2GB | 2-4GB | >4GB (누수 의심) |
| **Sharpe** | >2.0 | 1.0-2.0 | <1.0 |

---

> 문서 작성: 2026-03-15 | Phase S8 US-166
