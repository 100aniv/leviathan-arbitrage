# TF Semi-Final (SF) 2차 — System Validation

> **핵심 질문**: "24시간 동안 실제로 돈을 벌 수 있는가?"
> **검증일**: 2026-03-17
> **판정**: **FAIL** (Stage 2 PnL -$153.47)

---

## 단계 1-A: Delta Check — PASS
- QF 6차 커밋(47be694) 이후 변경: KillSwitchEvent fix만 (aba84b6)
- CRITICAL/HIGH 신규: 0

## 단계 1-B: 전략별 독립 검증 — PASS
- 10min Shadow: PnL +$59.80, 4전략 활성, crash=0

## 단계 1-C: 전략 상호작용 — PASS
- Strategy overlap: 0 (Prometheus 확인)
- PnL 무결성: 99.99%
- 8,555 trades, 30min

## 단계 2 Stage 1 (1H, 튜너 OFF) — PASS
- crash=0, 4전략 신호 흐름 정상, 10/10 거래소
- PnL 경고: -$79.99 (Stage 1은 PnL 기준 없음)

## 단계 2 Stage 2 (2H+, 튜너 OFF) — FAIL

| 항목 | 기준 | 결과 |
|------|------|------|
| PnL | > 0 | **-$153.47** ❌ |
| WR | > 60% | 90.7% ✅ |
| crash | = 0 | 0 ✅ |

### 2H45M 상세 데이터

| 항목 | 값 |
|------|-----|
| 실행 시간 | 06:43 ~ 09:29 (2h 45m) |
| 총 거래 | 45,659 |
| WR | 90.7% (41,424W / 4,249L) |
| PnL | -$153.47 |
| loss_capped (-$50) | 17건 = -$850 |
| 거래소 | 10/10 |

### 전략별 성과

| 전략 | 거래 | 손실 | 손실률 | 비고 |
|------|------|------|--------|------|
| cross_exchange_v1 | 31,540 | 2,193 | 7.0% | 소액 손실 누적 |
| futures_futures_v1 | 12,463 | 1,054 | 8.5% | loss_capped 17건 전부 여기 |
| spot_futures_v1 | 1,722 | 995 | 57.8% | 구조적 손실 |
| funding_rate_v1 | 16 | 15 | 93.8% | 비작동 |

---

## 근본 원인 분석

### 원인 1 (핵심 — 손실의 90%+): futures_futures stale data 진입
- stale detector가 17,937건 차단하지만 17건이 stale_threshold(3.0s)를 통과
- 통과한 stale orderbook이 fake spread(200+bps) 생성 → 진입 → 실제 가격 회귀 시 대형 손실
- loss_cap $50으로 캡되지만 17건 × $50 = $850
- WR 90.7%의 소액 이익($0.001~$0.01/건)으로는 회복 불가능
- **해결**: stale_threshold 1.5s 강화 + spread outlier filter + per-strategy circuit breaker

### 원인 2: spot_futures 구조적 손실 (WR 42%)
- Korean exchange(Upbit, Bithumb, Coinone) stale data로 basis 계산 부정확
- Phase S10에서 이미 식별된 문제이나 비활성화되지 않음
- **해결**: SHADOW_DISABLED_STRATEGIES에 spot_futures_v1 추가

### 원인 3: funding_rate 비작동 (WR 6.7%)
- 16건 중 15건 손실 — 거의 모든 진입이 역방향
- 신호 자체가 극소수이며 품질 낮음
- **해결**: SHADOW_DISABLED_STRATEGIES에 funding_rate_v1 추가

---

## 회귀 Phase S13 (5 US)

| US | 제목 | 핵심 |
|---|---|---|
| US-221 | futures stale guard 강화 (2차 freshness) | threshold 3.0→1.5s, spread outlier filter |
| US-222 | per-strategy circuit breaker | 연속 3건 손실 → 300s 쿨다운 |
| US-223 | spot_futures + funding_rate 비활성화 | WR 50% 미만 전략 제거 |
| US-224 | loss_cap 동적 조정 | $50→전략별 차등 ($10/$5) |
| US-225 | futures spread outlier filter | >100bps WARNING, >200bps 블랙리스트 |

---

**서명**: TF SF 2차 FAIL → Phase S13 회귀
