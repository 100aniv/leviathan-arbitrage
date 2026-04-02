# Phase-K_PLAN.md Code Review

**Review Date:** 2026-04-02  
**Reviewer:** Qwen Code  
**Document:** `docs/planning/Phase-K_PLAN.md`

---

## Executive Summary

| 심각도 | 이슈 수 | 주요 리스크 |
|--------|---------|-------------|
| **CRITICAL** | 5 | Live 전환 안전장치 미비, Gate 조건 모호 |
| **HIGH** | 8 | 보안/인프라/데이터 가정 과잉 |
| **MEDIUM** | 7 | 엣지케이스/운용 시나리오 누락 |
| **총계** | **20** | |

---

## CRITICAL (즉시 수정 필요)

### C-1. LiveGate Preflight 10 항목 PASS 기준 모호
**위치:** §3 Batch 4, US-055

**문제:**
- "API 연결", "잔고", "network latency" 등 항목만 나열되었을 뿐, **PASS 임계값이 정의되지 않음**
- 예: API 연결 — latency 몇 ms 이하? timeout 몇 초? 재시도 몇 회?
- 예: 잔고 — minimum balance 얼마 이상?
- 예: network latency — p50/p99 중 어떤 지표?

**위험:** Gatekeeper 역할 불가. 개발자 주관적 판단으로 Live 진입 가능.

**권고:**
```markdown
| 항목 | PASS 기준 | 측정 방법 |
|------|-----------|-----------|
| API 연결 | latency < 500ms (p99), timeout 5 초 | ping 100 회 |
| 잔고 | total_equity > $100 | balance API 호출 |
| network latency | p99 < 300ms | exchange endpoint 직접 측정 |
```

---

### C-2. US-358 `record_execution` Shadow→Live 복사 위험
**위치:** §2 US-358, §4 핵심 기술 결정

**문제:**
- `shadow.py:1603` 패턴을 live.py 에 복사하라고 명시
- **Shadow 와 Live 는 오류 처리, 트랜잭션 안전성, 롤백 요구사항이 근본적으로 다름**
- Shadow 는 데이터 기록만, Live 는 실제 자금 이동 — 동일 로직 사용은 위험

**위험:** 
- Live 환경에서 예외 발생 시 자금 손실 가능성
- partial fill, order cancel/replace 시나리오 미고려

**권고:**
- Live 전용 `LiveExecutionRecorder` 클래스 분리
- 트랜잭션 로그 (before/after snapshot) 기록 mandatory
- rollback/retry 로직 명시

---

### C-3. US-332 Paper 24H Sharpe≥2.0 단독 기준
**위치:** §2 US-332, §3 Batch 0

**문제:**
- Sharpe ratio 만으로 24H 무중단 성공 판단
- **crypto returns 는 fat-tailed 분포** — Sharpe 는 정규분포 가정
- max_drawdown_duration, tail_risk (CVaR), regime_change 고려 없음

**위험:** Sharpe 높지만 tail risk 큰 전략이 Paper 통과 → Live 에서 폭망

**권고:**
```markdown
- Sharpe ≥ 2.0 **AND**
- max_drawdown_duration < 4H **AND**
- CVaR(99%) < 5% **AND**
- trade_count ≥ 50 (통계적 유의성)
```

---

### C-4. US-364 iMessage 승인 게이트 fallback 로직 부재
**위치:** §3 Batch 3, §5 위험 요소

**문제:**
- "AppleScript + Telegram DevBot fallback" 언급되었으나
- **언제 fallback 으로 전환하는지 자동화 로직 없음**
- "macOS 업데이트로 AppleScript 깨짐" — 수동 감지 후 수동 전환?

**위험:** 
- iMessage 실패 시 Paper/Live 진입이 무한 대기
- 야간/주말에 수동 개입 필요

**권고:**
```python
def send_approval_request():
    if macos_available():
        result = try_applescript(timeout=30)
        if result.failed:
            log.warning("iMessage failed, fallback to Telegram")
            result = try_telegram_bot()
    else:
        result = try_telegram_bot()
    return result
```

---

### C-5. US-056 첫 Live 체결 성공/실패 기준 부재
**위치:** §2 US-056, §3 Batch 4

**문제:**
- "Live 체결 로그 1 건" 이 완료 기준
- **어떤 전략으로, 어떤 크기로, 어떤 조건에서 첫 체결하는지 정의 없음**
- 실패 시 (예: slippage过大, partial fill) 재시도 기준도 없음

**위험:** 
- 의미 없는 소량 체결로 Phase K 완료 처리 가능
- 실제 운용 의도와 다른 첫 거래

**권고:**
```markdown
- 첫 Live 체결 전략: 가장 trade_count 많은 전략 (Phase J 기준)
- 체결 크기: engine.json capital 의 1% (위험 제한)
- 성공 기준: full_fill, slippage < 0.1%, 기록 완료
- 실패 시: 3 회 재시도 후 Phase K 보류
```

---

## HIGH (중요 개선 필요)

### H-1. US-334 capital 하드코딩 — exchange 최소주문량 무시
**위치:** §2 US-334

**문제:**
- `spot_usd=20, futures_usd=30, spot_krw=28000` 하드코딩
- **Exchange 별 minimum_order_size 고려 없음**
  - 예: 어떤 exchange 는 $50 이상 주문 불가
  - 예: KRW 시장은 1,000 원 단위 주문

**위험:** 자본 할당했더라도 실제 주문 불가 → 전략 실행 불가

**권고:**
```json
{
  "capital": {
    "spot_usd": 20,
    "futures_usd": 30,
    "spot_krw": 28000
  },
  "exchange_limits": {
    "MEXC": {"min_order_usd": 5},
    "Gate.io": {"min_order_usd": 10},
    "Upbit": {"min_order_krw": 5000}
  }
}
```

---

### H-2. US-359 API 키 18 개 필드 — 보안 요구사항 누락
**위치:** §2 US-359, §3 Batch 1

**문제:**
- `.env` 에 18 개 API 키 저장 계획
- **다음 항목이 모두 누락됨:**
  - Key rotation 전략
  - Permission scoping (read-only vs trade)
  - Encryption at rest
  - Key validation (만료/잘못된 키 사전 감지)

**위험:** 
- .env 유출 시 15 개 exchange 전체 자금 위험
- 잘못된 키로 runtime 에만 오류 발생

**권고:**
```markdown
- .env.gpg 암호화 + preflight 에서 키 유효성 검증
- read-only 키와 trade 키 분리 (가능 exchange)
- key_rotation.md 문서 추가 (90 일 주기)
```

---

### H-3. US-360 Tier4 어댑터 5 개 — 공통 인프라 미정의
**위치:** §2 US-360, §3 Batch 2

**문제:**
- 5 개 어댑터 병렬 개발
- **공통 요구사항이 정의되지 않음:**
  - Rate limiting 전략 (exchange 별 API call 제한)
  - Unified error handling (exchange 별 오류 코드 매핑)
  - Health check 메커니즘
  - Reconnect/backoff 정책

**위험:** 
- 어댑터마다 다른 동작 → 디버깅 불가
- Rate limit 초과로 IP 밴

**권고:**
- `NativeAdapterBase` 에 rate_limiter, error_mapper, health_check 추상메서드 추가
- 5 개 어댑터 개발 전 base 클래스 먼저 확정

---

### H-4. US-362 OHLCV synthetic spread ±0.05% 임의 가정
**위치:** §2 US-362, §4 핵심 기술 결정

**문제:**
- `bid = mid * 0.9995`, `ask = mid * 1.0005`
- **근거 없음:** 실제 orderbook depth, volatility, liquidity 고려하지 않음
- 시장 상황에 따라 spread 는 0.01%~1% 까지 변동

**위험:** 
- Backtest/Paper 에서 실제와 다른 slippage
- Live 에서 예상치 못한 손실

**권고:**
```python
# exchange 별 historical spread percentile 사용
spread_bps = historical_spread_percentile(exchange, pair, p90)
bid = mid * (1 - spread_bps / 10000)
ask = mid * (1 + spread_bps / 10000)
```

---

### H-5. US-361 BacktestResult meta 5 필드 — 집계 기준 불명확
**위치:** §2 US-361

**문제:**
- `total_trades`, `sharpe`, `mdd`, `win_rate`, `profit_factor`
- **전략별 집계인가, 전체 집계인가?**
- **시간 범위 (24H/7D/30D) 는?**

**위험:** 
- 다른 시간 범위/집계 기준으로 비교 불가
- API consumer 가 오해할 소지

**권고:**
```json
{
  "meta": {
    "total_trades": 1523,
    "sharpe": 2.34,
    "mdd": 0.043,
    "win_rate": 0.67,
    "profit_factor": 1.89,
    "aggregation": "per_strategy",
    "time_range": "24H",
    "strategies": ["triangular", "cross_exchange", ...]
  }
}
```

---

### H-6. US-363 POST /api/paper/start — 멱등성/상태 관리 누락
**위치:** §2 US-363

**문제:**
- Paper 모드 이미 실행 중일 때 재호출하면?
- 동시 요청 (concurrent requests) 은 어떻게 처리?
- Paper 모드 stop 엔드포인트는?

**위험:** 
- 중복 Paper 인스턴스 실행 → 자원 낭비
- 상태 불일치 (running 인데 stopped 로 표시)

**권고:**
```markdown
- 멱등성: 이미 실행 중이면 200 OK + 현재 상태 반환 (409 아님)
- 동시 요청: 분산 lock (Redis) 또는 단일 인스턴스 제한
- POST /api/paper/stop 엔드포인트 추가
```

---

### H-7. triangular synthetic data — 3-pair 가용성 가정
**위치:** §5 위험 요소

**문제:**
- "3-pair OHLCV 가용 여부 사전 확인" 언급만 있고 **구현 계획 없음**
- 한 pair 라도 데이터 없으면 triangular arbitrage 백테스트 불가

**위험:** 
- 백테스트 중간 실패 → Phase K 지연
- 잘못된 데이터로 백테스트 (한 pair 는 stale data)

**권고:**
```python
def validate_triangular_pairs(pairs: List[str]) -> ValidationResult:
    for pair in pairs:
        if not ohlcv_available(pair, lookback_days=7):
            return ValidationResult.fail(f"{pair} data missing")
    return ValidationResult.pass()
```

---

### H-8. 15 exchanges × 7 strategies — 자원/우선순위 계획 없음
**위치:** §1 Phase K 개요

**문제:**
- 105 개 조합을 모두 테스트하는가?
- **CI 리소스, 실행 시간, 우선순위 정의 없음**
- Phase K 기간 (언제까지?) 도 명시되지 않음

**위험:** 
- 테스트 실행 시간 초과 (수십 시간)
- 중요 전략/exchange 에 리소스 부족

**권고:**
```markdown
- Tier 1: 5 exchanges × 3 strategies (우선, 80% coverage)
- Tier 2: 10 exchanges × 4 strategies (선택적)
- Phase K 기간: 2026-04-XX ~ 2026-04-YY (5 영업일)
```

---

## MEDIUM (개선 권장)

### M-1. KillSwitch/CircuitBreaker/Guardian 트리거 조건 미정의
**위치:** §3 Batch 4 US-055

**문제:**
- 항목만 나열, **어떤 조건에서 발동하는지 정의 없음**
- 예: KillSwitch — MDD 10%? 20%? 연속 손실 5 회?

**권고:**
```markdown
| 컴포넌트 | 트리거 조건 | 액션 |
|----------|-------------|------|
| KillSwitch | MDD > 10% OR 연속손실 10 회 | 모든 포지션 청산, 거래 중지 |
| CircuitBreaker | 1H 손실 > 5% | 1H 거래 중지 |
| Guardian | slippage > 0.5% | 해당 exchange 주문 보류 |
```

---

### M-2. Telegram 알림 — 빈도/심각도/레이트리밋 없음
**위치:** §3 Batch 4 US-055

**문제:**
- "Telegram 알림" 항목만 있음
- **알림 빈도 (실시간? 1H 요약?), 심각도 (info/warn/error), rate limit 정의 없음**

**위험:** 
- 알림 폭포로 중요한 메시지 놓침
- Telegram API rate limit 초과

**권고:**
```markdown
- 심각도: error (즉시), warn (15 분 요약), info (1H 요약)
- Rate limit: 최대 10 메시지/분, 초과시 queue
```

---

### M-3. Shadow 13 항목 복합지표 — 가중치/trade-off 불명확
**위치:** §7 완료 기준 #2

**문제:**
- "13 항목 복합지표 PASS"
- **13 항목 중 일부만 실패하면? 가중치는?**
- 예: Sharpe 높지만 MDD 초과하면 PASS/FAIL?

**권고:**
```markdown
- Must-pass (0 개까지 실패): MDD<5%, crash=0, API 연결
- Should-pass (1 개까지 실패): Sharpe≥2.0, PF>1.2, win_rate>50%
- Nice-to-have (2 개까지 실패): 기타 7 항목
```

---

### M-4. Batch 내 실패 처리 — halt vs continue 명확화 필요
**위치:** §3 실행 순서

**문제:**
- Batch 2: `US-360 ‖ US-362 ‖ US-363` 병렬
- **하나 실패 시 나머지 계속하는가, 전체 Batch 중단인가?**

**권고:**
```markdown
- Batch 내 독립 US: 하나 실패해도 나머지 계속 (로그 기록)
- Batch 내 의존 US: 선행 US 실패 시 후행 자동 중단
- Batch 완료 시 통합 보고 (pass/fail/skipped)
```

---

### M-5. 네트워크 지연 측정 — 기준/위치 명시 필요
**위치:** §3 Batch 4 US-055

**문제:**
- "network latency" 항목
- **어디에서 어디까지 측정?**
  - 로컬 → exchange API?
  - 로컬 → WebSocket?
  - VPS → exchange?

**권고:**
```markdown
- 측정 위치: 실행 환경 (로컬/VPS) → exchange endpoint
- 지표: p50, p95, p99 (100 회 ping)
- PASS: p99 < 300ms
```

---

### M-6. Phase K → L 전환 — Exit Gate 외 추가 조건 필요
**위치:** §7 완료 기준

**문제:**
- 6 개 Exit Gate 조건만 있음
- **Phase L (다음 단계) 의 Entry Gate 와 정합성 확인 필요**
- Phase L 이 무엇을 요구하는지 명시되지 않음

**권고:**
- Phase L_PLAN.md 참조하여 Entry Gate 와 정합성 표 추가
- 예: "Phase L Entry Gate #3 = Phase K Exit Gate #5" 매핑

---

### M-7. Telegram DevBot 승인 — 워크플로우/감사 로그 누락
**위치:** §3 Batch 3 US-364, §5 위험 요소

**문제:**
- "Telegram DevBot (`/approve` 명령)"
- **누가 승인하는가? (1 인? 다수?)**
- **감사 로그는 어디에 기록되는가?**
- 승인 timeout 은? (무한 대기?)

**권고:**
```markdown
- 승인자: @admin1, @admin2 (2 인 중 1 인)
- 감사 로그: logs/approval_{timestamp}.json
- Timeout: 30 분 (초과시 자동 reject)
```

---

## Summary Action Items

| 우선순위 | 액션 | 담당 | 기한 |
|----------|------|------|------|
| **P0** | LiveGate Preflight 10 항목 PASS 기준 구체화 | planner | Phase K 시작 전 |
| **P0** | US-358 Live 전용 execution recorder 분리 설계 | engineer | US-358 착수 전 |
| **P0** | US-332 Paper 성공 기준 multi-metric 으로 확장 | planner | US-332 착수 전 |
| **P1** | API 키 보안 요구사항 (rotation, encryption) 문서화 | security | US-359 착수 전 |
| **P1** | Tier4 어댑터 base 클래스 공통 인프라 확정 | architect | US-360 착수 전 |
| **P1** | iMessage→Telegram 자동 fallback 로직 구현 | engineer | US-364 착수 전 |
| **P2** | Batch 내 실패 처리 정책 명문화 | planner | Phase K 시작 전 |
| **P2** | Phase K → L Entry/Exit Gate 정합성 표 추가 | planner | Phase K 완료 전 |

---

## Conclusion

Phase-K_PLAN.md 는 **종합 테스트 사이클의 큰 그림은 잘 제시**하고 있으나, **Live 전환이라는 중대한 단계를 다루기에는 안전장치와 구체성이 부족**합니다. 특히 CRITICAL 이슈 5 개 (Gate 기준 모호, Shadow→Live 복사 위험, Sharpe 단독 기준, fallback 로직 부재, 첫 Live 성공 기준 없음) 는 **Phase K 시작 전에 반드시 수정**되어야 합니다.

**권장 액션:**
1. 본 CR 문서를 Phase-K_PLAN.md 에 반영 (v2 업데이트)
2. CRITICAL 5 개 항목 수정 후 팀 리뷰
3. 수정본으로 Phase K 착수
