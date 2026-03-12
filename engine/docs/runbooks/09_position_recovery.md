# Runbook 09 — 포지션 복구 (Position Recovery)

**Severity:** CRITICAL (재무 영향 가능)
**SLA:** 감지 1분 이내 Kill Switch 발동. 포지션 확인 20분 이내. 복구 완료 60분 이내.
**적용 버전:** Shadow/Paper/Live 모드, VirtualBalanceTracker, atomic_executor

---

## 개요

포지션 불일치란 LEVIATHAN 엔진의 내부 추적 상태와 실제 거래소 잔고/주문 상태가 다른 상황이다.

**주요 발생 원인**:
- 엔진 크래시 중 거래 실행 완료 (부분 체결)
- 거래소 API 응답 타임아웃 후 실제로는 주문이 체결됨
- 네트워크 단절 중 양방향 아비트리지 한쪽 레그만 실행
- DB 장애 중 체결 기록 소실
- Kill Switch 발동 직전 제출된 주문 처리 중

---

## 즉각 조치 (1분 이내)

```bash
# 1. Kill Switch 활성화 (신규 거래 즉시 차단)
JWT_TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${DASHBOARD_USER}&password=${DASHBOARD_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8000/risk/kill-switch/activate \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "position_mismatch_investigation"}'

# 2. Kill Switch 활성 확인
curl -s http://localhost:8000/risk/metrics | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
  print('KS Active:', d.get('kill_switch_active'))"
# 기대: KS Active: True
```

---

## 미체결 주문 확인 (거래소 API)

```bash
# 1. DB에서 SUBMITTED 상태 주문 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
    id,
    exchange_id,
    symbol,
    side,
    amount,
    status,
    ts
FROM execution_log
WHERE status = 'SUBMITTED'
  AND ts > NOW() - INTERVAL '2 hours'
ORDER BY ts DESC;
" 2>/dev/null

# 2. 최근 1시간 모든 주문 상태 집계
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, status, COUNT(*) AS cnt
FROM execution_log
WHERE ts > NOW() - INTERVAL '1 hour'
GROUP BY exchange_id, status
ORDER BY exchange_id, status;
" 2>/dev/null

# 3. 엔진 내부 포지션 (VirtualBalanceTracker / Shadow 상태)
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary | python3 -m json.tool
```

---

## 잔고 불일치 감지

### 엔진 잔고 vs 거래소 실제 잔고 비교

엔진 API로 집계된 잔고와 각 거래소 웹/앱에서 직접 확인한 잔고를 대조한다.

```bash
# 거래소별 엔진 추적 잔고
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/exchanges | python3 -m json.tool
```

| 거래소 | 확인 항목 |
|--------|-----------|
| Binance | 현물 + Futures 잔고, 미체결 주문 |
| Bybit | 현물/선물 잔고 |
| OKX | Trading Account 잔고 |
| Bitget | Spot 잔고 |
| Upbit | KRW + 코인 잔고 |
| Bithumb | KRW + 코인 잔고 |
| Coinone | KRW + 코인 잔고 |

**불일치 판정 기준**: 엔진 추적값과 거래소 실제값의 차이가 포지션 크기의 1% 초과 시 불일치로 판정.

---

## 불일치 유형별 복구

### 유형 A: 한쪽 레그만 체결 (부분 아비트리지)

**증상**: 한 거래소에서 매수/매도가 실행됐으나 반대 거래소에 대응 레그 없음

```bash
# DB에서 단독 체결 주문 확인 (pair 없는 거래)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
    id,
    exchange_id,
    symbol,
    side,
    amount,
    status,
    ts
FROM execution_log
WHERE ts > NOW() - INTERVAL '2 hours'
  AND status IN ('SUCCESS', 'FILLED')
ORDER BY ts DESC
LIMIT 30;
" 2>/dev/null
```

**복구 절차**:
```bash
# 1. 해당 포지션 수동 청산 (거래소 웹에서 직접 주문)
# 2. DB에 수동 정정 레코드 업데이트
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
UPDATE execution_log
SET status = 'MANUAL_CLOSED',
    notes = 'position_recovery_runbook_09'
WHERE id = '<trade_id_here>';
" 2>/dev/null

echo "유형 A 복구 완료: <trade_id_here> → MANUAL_CLOSED"
```

### 유형 B: 중복 주문 발생

**증상**: 동일 신호에 대해 주문이 2회 이상 실행됨

```bash
# 중복 주문 탐지 (같은 거래소/심볼/방향이 1시간 내 복수 체결)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
    exchange_id,
    symbol,
    side,
    COUNT(*) AS cnt,
    MIN(ts)  AS first_at,
    MAX(ts)  AS last_at,
    SUM(amount) AS total_amount
FROM execution_log
WHERE ts > NOW() - INTERVAL '1 hour'
  AND status IN ('SUCCESS', 'FILLED')
GROUP BY exchange_id, symbol, side
HAVING COUNT(*) > 1
ORDER BY cnt DESC;
" 2>/dev/null
```

**복구 절차**:
```bash
# 1. 중복 포지션 중 하나를 거래소에서 직접 청산
# 2. DB에서 중복 레코드 정정 (최신 1건만 남김)
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
UPDATE execution_log
SET status = 'MANUAL_CLOSED',
    notes = 'duplicate_order_recovery'
WHERE id IN (
  -- 중복 중 정정 대상 id를 직접 지정
  '<duplicate_trade_id>'
);
" 2>/dev/null
```

### 유형 C: DB 기록 소실 (체결됐으나 DB에 없음)

**증상**: 거래소 잔고는 변경됐으나 DB execution_log에 해당 기록 없음

```bash
# 각 거래소 API에서 직접 거래 이력 조회 (수동)
# Binance: GET /api/v3/myTrades?symbol=BTCUSDT&startTime=<unix_ms>
# Bybit:   GET /v5/execution/list?category=spot&symbol=BTCUSDT
# OKX:     GET /api/v5/trade/fills?instType=SPOT

# 확인된 거래를 DB에 수동 삽입
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
INSERT INTO execution_log
  (exchange_id, symbol, side, amount, price, status, ts, notes)
VALUES
  ('binance', 'BTC/USDT', 'buy', 0.001, 45000.0,
   'MANUAL_RECOVERY', NOW(),
   'manual_recovery_runbook_09_db_loss');
" 2>/dev/null

echo "유형 C 복구: 수동 삽입 완료"
```

### 유형 D: VirtualBalanceTracker 불일치

**증상**: Shadow/Paper 모드에서 `/api/v1/portfolio-summary` 잔고와 거래소 실제 잔고 불일치

```bash
# Shadow 모드는 가상 잔고이므로 실제 재무 영향 없음
# 엔진 재시작으로 VirtualBalanceTracker 리셋

docker compose restart leviathan-engine
sleep 30

# 재시작 후 잔고 재확인
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary | python3 -m json.tool

echo "유형 D 복구: VirtualBalanceTracker 리셋 완료"
```

---

## VirtualBalanceTracker 리셋 절차

Shadow/Paper 모드에서 잔고 상태가 오염된 경우:

```bash
# 1. 현재 Shadow 잔고 스냅샷 저장
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary \
  > /tmp/balance_snapshot_$(date +%Y%m%d_%H%M%S).json

# 2. 엔진 재시작 (VirtualBalanceTracker는 인메모리 — 재시작으로 초기화)
docker compose restart leviathan-engine
sleep 30

# 3. 초기화 확인
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/api/v1/portfolio-summary

# 4. 초기 잔고 설정 확인
docker compose exec leviathan-engine env | \
  grep -E "INITIAL_BALANCE|PAPER_BALANCE"
```

---

## 재시작 후 검증

복구 작업 완료 후 아래 검증을 순서대로 실행한다.

```bash
echo "=== Runbook 09 복구 완료 확인 ==="

# 1. 미체결 주문 0건
OPEN_COUNT=$(docker compose exec -T timescaledb psql -U leviathan -d leviathan -t -c "
SELECT COUNT(*) FROM execution_log
WHERE status = 'SUBMITTED'
  AND ts > NOW() - INTERVAL '24 hours';" 2>/dev/null | tr -d ' ')
echo "미체결 주문: ${OPEN_COUNT}건 (0이어야 정상)"

# 2. 거래소 연결 상태
curl -s -H "Authorization: Bearer $JWT_TOKEN" \
  http://localhost:8000/exchanges | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ex in data.get('exchanges', []):
    print(f\"{ex['exchange']}: {ex.get('status', 'unknown')}\")" 2>/dev/null

# 3. 데이터 수신 정상
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT exchange_id, NOW() - MAX(ts) AS gap
FROM market_ticks
GROUP BY exchange_id
ORDER BY gap DESC;" 2>/dev/null

# 4. Kill Switch 해제 (복구 완료 확인 후에만)
read -p "모든 불일치 해소 확인됐습니까? [y/N]: " confirm
if [ "$confirm" = "y" ]; then
    curl -s -X POST http://localhost:8000/risk/kill-switch/deactivate \
      -H "Authorization: Bearer $JWT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"reason": "position_recovery_completed_runbook_09"}'
    echo "Kill Switch 해제 완료"
else
    echo "Kill Switch 유지 — 추가 조사 필요"
fi
```

---

## 포지션 복구 기록 양식

발생 즉시 아래 양식으로 기록 후 팀에 공유:

```
발생 일시:
발견 일시:
감지 방법: (Telegram 알림 / 모니터링 / 수동 발견)
영향 거래소:
영향 심볼:
불일치 유형: (A: 한쪽 레그 / B: 중복 주문 / C: DB 소실 / D: 가상 잔고)
모드: (Shadow / Paper / Live)
영향 금액 (USDT 환산):
원인:
복구 조치:
복구 완료 일시:
담당자:
재발 방지:
```

---

## 예방 조치

1. **CircuitBreaker 설정**: `engine/.env`에서 `MAX_DAILY_LOSS_USD` 적절히 설정
2. **잔고 자동 리콘실리에이션**: 엔진이 5분마다 내부 상태와 DB를 자동 대조
3. **atomic_executor 활용**: 양방향 레그를 원자적으로 실행 (`engine/src/execution/atomic_executor.py`)
4. **Shadow 모드 우선 검증**: 실거래(live) 전 LiveGate 6-check 전량 통과 필수
5. **Bithumb stale data 주의**: 소형코인 증분 orderbook에서 2~10x 가격 오차 → 시그널에서 제외 처리됨

---

## 에스컬레이션

| 상황 | 조치 |
|------|------|
| Live 모드 포지션 불일치 | 즉시 개발팀 + 거래팀 호출 |
| 불일치 금액 > $500 | 상위 관리자 보고 |
| 원인 불명 30분 초과 | 전체 실거래 중단, 조사팀 구성 |
| DB 데이터 소실 확인 | Runbook 04 (Database Recovery) 병행 |
| 엔진 반복 크래시 | Runbook 08 (Server Crash) 병행 |

---

## References

- Server Crash: `engine/docs/runbooks/08_server_crash.md`
- Database Recovery: `engine/docs/runbooks/04_database_recovery.md`
- Kill Switch: `engine/docs/runbooks/01_kill_switch_recovery.md`
- Exchange Outage: `engine/docs/runbooks/02_exchange_outage.md`
- Incident Response: `engine/docs/runbooks/07_incident_response.md`
- Operations Guide: `docs/operations/operations-guide.md`
- atomic_executor: `engine/src/execution/atomic_executor.py`
