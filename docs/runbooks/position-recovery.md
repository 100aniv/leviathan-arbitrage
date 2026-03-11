# Runbook: 포지션 불일치 복구

> **카테고리**: 거래 일관성
> **심각도**: P1 (재무 영향 가능)
> **예상 복구 시간**: 20~60분
> **최종 수정**: 2026-03-11

---

## 개요

포지션 불일치란 LEVIATHAN 엔진의 내부 포지션 상태와 실제 거래소 잔고/오더 상태가 다른 상황을 의미합니다.

**주요 발생 원인**:
- 엔진 크래시 중 거래 실행 완료 (부분 체결)
- 거래소 API 응답 타임아웃 후 실제로는 주문이 체결됨
- 네트워크 단절 중 양방향 아비트리지 한쪽만 실행
- DB 장애 중 체결 기록 소실

---

## 즉각 조치 (1분 이내)

```bash
# 1. Kill Switch 활성화 (신규 거래 즉시 중단)
curl -X POST http://localhost:8000/risk/kill-switch/activate \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "position_mismatch_investigation"}'

# 2. Kill Switch 확인
curl -s http://localhost:8000/risk/metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('KS:', d.get('kill_switch_active'))"
# 예상 출력: KS: True
```

---

## 진단 절차

### 1단계: 엔진 내부 포지션 확인

```bash
# Shadow 모드 포지션 (VirtualBalanceTracker 기반)
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary | python3 -m json.tool

# 거래소별 잔고
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/exchanges | python3 -m json.tool
```

### 2단계: DB 기록 확인

```bash
# 최근 1시간 체결 기록
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
  id,
  exchange,
  symbol,
  side,
  amount,
  price,
  filled,
  status,
  created_at
FROM trades
WHERE created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC
LIMIT 50;"
```

### 3단계: 미체결 주문 확인

```bash
# DB에서 open/partial 상태 주문 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT id, exchange, symbol, side, amount, filled, status, created_at
FROM trades
WHERE status IN ('open', 'partial')
  AND created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;"
```

### 4단계: 거래소별 실제 잔고 조회 (수동)

크래시/장애 발생 시점의 거래소 실제 잔고를 각 거래소 웹사이트/앱에서 확인:

| 거래소 | 확인 항목 |
|--------|-----------|
| Binance | 현물 잔고 + 미체결 주문 |
| Bybit | 현물/선물 잔고 |
| OKX | Trading Account 잔고 |
| Bitget | Spot 잔고 |
| Upbit | KRW + 코인 잔고 |
| Bithumb | KRW + 코인 잔고 |
| Coinone | KRW + 코인 잔고 |

---

## 불일치 유형별 복구

### 유형 A: 한쪽 레그만 체결 (부분 아비트리지)

**증상**: 한 거래소에서 매수/매도가 실행되었으나 반대쪽 레그 없음

```bash
# DB에서 pair 없는 단독 거래 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
  t1.id as trade_id,
  t1.exchange,
  t1.symbol,
  t1.side,
  t1.amount,
  t1.status,
  t1.created_at
FROM trades t1
WHERE t1.created_at > NOW() - INTERVAL '2 hours'
  AND t1.status = 'closed'
ORDER BY t1.created_at DESC;"

# 복구 절차:
# 1. 해당 포지션 수동 청산 (거래소 웹에서 직접)
# 2. DB에 수동 정정 레코드 삽입
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
UPDATE trades
SET status = 'manual_closed', notes = 'position_recovery_runbook'
WHERE id = '<trade_id>';"
```

### 유형 B: 중복 주문 발생

**증상**: 동일 신호에 대해 주문이 2회 실행됨

```bash
# 중복 주문 탐지
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
  exchange,
  symbol,
  side,
  COUNT(*) as count,
  MIN(created_at) as first_at,
  MAX(created_at) as last_at
FROM trades
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY exchange, symbol, side
HAVING COUNT(*) > 1
ORDER BY count DESC;"

# 복구 절차:
# 1. 중복된 포지션 중 하나를 거래소에서 직접 청산
# 2. DB 정정
```

### 유형 C: DB 기록 소실 (체결됐으나 DB 없음)

**증상**: 거래소 잔고는 변경됐으나 DB에 기록 없음

```bash
# 잔고 변화량으로 추정 거래 재구성
# (각 거래소 API에서 직접 거래 이력 조회)

# Binance 예시 (API 직접 호출):
# GET /api/v3/myTrades?symbol=BTCUSDT&startTime=<timestamp>

# 복구 후 수동 DB 삽입
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
INSERT INTO trades (exchange, symbol, side, amount, price, filled, status, created_at, notes)
VALUES ('binance', 'BTC/USDT', 'buy', 0.001, 45000.0, 0.001, 'manual_closed', NOW(), 'manual_recovery_runbook');"
```

### 유형 D: VirtualBalanceTracker 불일치

**증상**: Shadow 모드에서 `/api/v1/portfolio-summary`의 잔고와 거래소 실제 잔고 불일치

```bash
# 엔진 재시작으로 VirtualBalanceTracker 리셋
# (Shadow 모드이므로 실제 재무 영향 없음)
docker compose restart engine
sleep 30

# 재시작 후 잔고 재확인
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary
```

---

## 복구 완료 확인

```bash
echo "=== 포지션 복구 완료 확인 ==="

# 1. 미체결 주문 0건 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT COUNT(*) as open_trades
FROM trades
WHERE status IN ('open', 'partial')
  AND created_at > NOW() - INTERVAL '24 hours';" 2>/dev/null

# 2. 거래소 연결 상태
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/exchanges | python3 -c \
  "import sys,json; data=json.load(sys.stdin); \
  [print(f'{e[\"exchange\"]}: {e.get(\"status\",\"unknown\")}') for e in data.get('exchanges', [])]"

# 3. Kill Switch 해제 (복구 완료 확인 후)
read -p "모든 불일치 해소 확인됨? [y/N]: " confirm
if [ "$confirm" = "y" ]; then
  curl -X POST http://localhost:8000/risk/kill-switch/deactivate \
    -H "Authorization: Bearer $JWT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"reason": "position_recovery_completed"}'
  echo "Kill Switch 해제됨"
fi
```

---

## 포지션 복구 기록 양식

사고 발생 시 아래 양식으로 기록 후 팀에 공유:

```
발생 일시:
발견 일시:
영향 거래소:
영향 심볼:
불일치 유형: (A/B/C/D)
영향 금액 (USDT):
원인:
복구 조치:
복구 완료 일시:
담당자:
```

---

## 예방 조치

1. **CircuitBreaker 설정 확인**: `engine/.env`의 `MAX_DAILY_LOSS_USD` 적절히 설정
2. **주기적 잔고 리콘실리에이션**: 엔진이 자동으로 5분마다 실행 (`_start_background_tasks → reconcile`)
3. **Shadow 모드 우선**: 실거래(live) 전 충분한 Shadow 검증 (LiveGate 6-check 통과 필수)
4. **atomic_executor 활용**: 양방향 레그 원자적 실행 (`engine/src/execution/atomic_executor.py`)

---

## 에스컬레이션

| 상황 | 조치 |
|------|------|
| 실거래(LIVE) 포지션 불일치 | 즉시 개발팀 + 거래팀 호출 |
| 불일치 금액 > $500 | 상위 관리자 보고 |
| 원인 불명 30분 이상 | 전체 실거래 중단, 조사팀 구성 |

*관련 Runbook: [`server-crash-recovery.md`](server-crash-recovery.md) | [`operations-guide.md`](../operations/operations-guide.md)*
