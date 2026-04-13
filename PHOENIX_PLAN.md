# PHOENIX v3 — 카나리 실행 계획 (단일 SSOT)

> 400줄 이내. §1~6 = 영구 계획. §7 = 카나리 이력 요약 테이블.  
> 최종 수정: 2026-04-13 (v94 BUG-76 수정 기준 — adaptive exit threshold)

---

## §1. 목적 + Phase 개요

**목표**: 7거래소 × 4전략 실전 검증 후 풀 운영 전환.

| Phase | 내용 | 상태 |
|-------|------|------|
| 0 | 배관 준비 (모드분리, 자본%, 심볼제외) | ✅ 완료 |
| 1 | 배관 뚫기 (첫 Live 체결) | ✅ 완료 (live20, FR 1건) |
| 2 | 카나리 단계 확장 | 🔄 진행중 (Step 2-2) |
| 3 | 풀 통합 72H + 튜너 | ⏳ 대기 |

**현재**: Phase 2 Step 2-2 — FF(27bps) + FR(6.55bps) 동시 운영  
**PID**: 44772 | **버전**: v94 | **시작**: 2026-04-13 12:42 KST

---

## §2. 카나리 방법론 (확정)

### 원칙: 배관 검증 vs 전략 결과 기반

| 구분 | 목적 | 기간 | 성공 조건 |
|------|------|------|----------|
| 배관 검증 | 신전략 배선 확인 (신호→체결→정산) | 1H | crash=0, 체결≥1 (손실 허용) |
| 전략 검증 | 수익 조건 충족 시 자동 체결, 결과 누적 | 조건 기반 | crash=0, PnL>0 (3사이클 이상) |

**핵심**: BPS 임계값 = ON/OFF 게이트. 수익 조건에서만 체결 → 임계값 충족 전까지 손실 0.  
**24H 고정 기간 = 비효율**: 구조적으로 손실이 확정된 전략에 24H 소비 금지.

### 전략별 검증 완료 조건

| 전략 | 완료 조건 | 현재 |
|------|----------|------|
| futures_futures | spread_exit 3회 + crash=0 + PnL≥0 | spread_exit 0/3 (BUG-76 즉시청산 artifacts, v95에서 재검증) |
| funding_rate | 결산 3회 (UTC 0/8/16) + PnL>0 | 배관 완료, 기회 대기중 (<2bps) |
| spot_futures | 체결 5건 + PnL≥0 | Step 2-1.5 대기 |
| cross_exchange | 체결 1건 + kimchi premium 확인 | Step 2-3 대기 |

### Step 2-2 게이트 조건 (현재 스텝)

- crash=0, KillSwitch=0, CB OPEN < 5
- 손실 tier: $6 (총자본 $120의 5%)
- FF spread_exit 3회 확인 OR 시장 27bps+ 도달
- FR 결산 3회 (2026-04-13 16:00 UTC 기준 3번째)
- 완료 후 → Step 2-1.5로 **역행** (spot_futures 추가)

### Phase 2 전체 순서

```
Step 2-1:   FF 단독 (완료 — v85~v94)
Step 2-1.5: FF + SF  ← 다음
Step 2-2:   FF + FR  (현재)
Step 2-3:   + XE Bin↔Bitget (글로벌, KRW 제외)
Step 2-4:   + CE Coinone
Step 2-5:   + CE Upbit (키 갱신 필요)
Step 2-6:   + CE Bithumb
Step 2-8:   전체 72H 통합
```

---

## §3. 전략별 현황 + 임계값

### 활성 전략 (현재 v94)

#### futures_futures (FF)
- **임계값**: `min_spread_bps = 27` (engine.json + strategy_params.json)
- **계산 근거**: 왕복 수수료 22bps (Binance5+Bitget6=11bps × 2) + exit_threshold 4.5bps = 26.5 → 27
- **현재 시장**: 10~17bps (< 27bps → 자동 거절, 손실 없음)
- **배관**: v85~v93에서 완전 검증. 체결 200건+, rollback 경로 확인
- **다음 체결 조건**: 시장 스프레드 ≥ 27bps (주로 급변동 시간대)
- **미검증**: spread_exit 경로 (조기청산, 시장 조건 의존)

#### funding_rate (FR)
- **임계값**: `funding_min_diff_bps = 2.0` (engine.json), `min_funding_rate_bps = 6.55` (strategy_params)
- **현재 시장**: Binance↔Bitget 최대 1.71bps (ADA) — 전 심볼 2bps 미달
- **배관**: FundingRateCollector 462심볼 폴링 정상 (66초 주기)
- **다음 체결 조건**: diff ≥ 2bps (결산 직전 1~2시간에 확대 가능)
- **결산 일정**: 매일 UTC 00:00 / 08:00 / 16:00

### 비활성 전략 (Phase 2 순서대로 추가)

| 전략 | 상태 | 임계값 | 비고 |
|------|------|--------|------|
| spot_futures | DISABLED_PHASE2 | min_spread 12.39bps | Step 2-1.5 |
| cross_exchange | DISABLED_PHASE2 | min_spread 5bps | Step 2-3 |
| triangular | DISABLED_PHASE2 | — | Bithumb AuthCollector 미구현 |
| statistical_arb | DISABLED | — | 데이터 부족 |
| cex_dex | MONITOR | — | DEX 미연동 |

### 자본 배분 ($120 풀, 퍼센트 기반)

```
총자본: $120 (BinFut $20.45 + BitFut $33.00 + spot 잔고)
reserve_pct: 20%
funding_rate: 35% | futures_futures: 20% | spot_futures: 20% | cross_exchange: 25%
base_position_pct: 5% → 거래당 $6 (20x 레버리지 → 마진 $0.30)
min_trade_notional_usd: $5
```

### 거래소 구성

| 거래소 | Taker | 역할 |
|--------|-------|------|
| Binance Futures | 0.10% | FF + FR |
| Bitget Futures | 0.06% | FF + FR (BUG-20 수정) |
| Binance Spot | 0.10% | SF + XE |
| Bitget Spot | 0.10% | SF + XE |
| Upbit | 0.139% | CE (KRW) |
| Coinone | 0.02% | CE (KRW, API할인) |
| Bithumb | 0.25% | CE (KRW, stale guard) |

---

## §4. 카나리 이력 요약

| 버전 | 기간 | 전략 | Fills | PnL | 종료 사유 |
|------|------|------|-------|-----|----------|
| live20 | 2026-04-07 | FR | 1 | -$0.31 | Phase1 완료 |
| v1~v4 | 2026-04-08 | FF | 4 | — | Bug25a/b/c (same-ex check, rollback) |
| v5~v9 | 2026-04-09 | FF | 6 | — | BUG-A~G (자본$1.20, spread 150bps) |
| v10~v17 | 2026-04-09~11 | FF | 193 | -$1.90 | Bug28~32 수정 후 안정화 |
| v18~v84 | 2026-04-11~12 | FF | 200+ | 누적손실 | 레이턴시 3-4초, 체결빈도 이슈 |
| v85~v91 | 2026-04-12 | FF | 12 | -$0.04 | min_spread 재조정 과정 |
| v92 | 2026-04-13 | FF | 0 | $0.00 | min_spread=30bps → 시장없음 |
| v93 | 2026-04-13 | FF | 193 | -$1.17 | **BUG-73**: 15bps → 22bps 수수료 미반영 |
| **v94** | 2026-04-13~ | FF+FR | 28 | -$0.12 | **진행중** |

> v94 (13:28 KST): FF fills=6 (COMP,CHZ,AAVE×2,ENA + AAVE시간청산), FR positions=3 (LA,MOVE,0G), crash=0, KillSwitch=0  
> BUG-74 발견: margin guard 없어 0G/USDT FR retry loop 185회. BUG-75: max_hold=1800s (config 300 미반영)  
> 다음: COMP/CHZ 13:32 타임아웃 → FR 결산 UTC 16:00 (1차)

---

## §5. 미해결 버그

| ID | 파일 | 설명 | 영향 | 우선순위 |
|----|------|------|------|---------|
| BUG-73 | futures_futures.py:630 | gate `entry_only=True` → 입장비용만 계산, 퇴장비 미포함 | 구조적 손실 (15bps에서 -7bps) | **P0 → 수정완료** (27bps로 우회) |
| collision_key | live.py:~1595 | `_build_collision_key`에 `strategy_id` 없음 → 멀티전략 동일 심볼 충돌 | Step 2-2+ 에서 FF/FR 동시체결 시 중복 방지 실패 | **P1 → 수정완료** (v94, tests pass) |
| spread_exit | futures_futures.py:406 | 조기청산 경로 미검증 | FF 포지션 만기 외 청산 경로 미확인 | **P1 → 0/3** (v94 exits = BUG-76 artifacts, v95 재검증 필요) |
| BUG-74 | live.py | margin_guard 없음 → Binance margin < $3 시 신규 ENTRY 허용 | FR 0G/USDT -2019 retry loop 185+회 (30s cooldown만 있음) | **P1** (v95 전 수정) |
| BUG-75 | futures_futures.py:94 | `max_hold_seconds` config 캐시 이슈 → 1800s 사용 (engine.json=300) | FF 포지션 30분 보유, 5분 의도 → 마진 고갈 (4포지션 동시 보유) | **P1** (v95 재시작 시 자동 해결) |
| BUG-76 | futures_futures.py:406,224 | 적응형 exit_threshold (p50) > min_spread_bps (27bps) → 진입 즉시 손절 청산 | FF 모든 포지션 진입 즉시 exit → 수수료 손실 반복 | **P0 → 수정완료** (2026-04-13, static 4.05bps 고정) |
| WS reconnect | binance/binance_futures | "no close frame" 간헐적 발생 (10회/12분) | 자동 재연결, 운영 영향 없음 | P2 |

### BUG-74 수정 방법 (v95 적용)
```python
# live.py _execute_trade_request에서 — symbol cooldown 체크 직후
if not _is_close_req:
    _MIN_MARGIN_ENTRY = 3.0
    for leg in trade_request.legs:
        if "futures" in leg.exchange_id:
            _m = float(self._cached_margin.get(leg.exchange_id, float('inf')))
            if _m < _MIN_MARGIN_ENTRY:
                logger.warning("live_mode.entry_blocked_margin_low ex=%s margin=%.2f", leg.exchange_id, _m)
                self._notify_pre_exec_rollback(trade_request, sid)
                return
```

---

## §6. 리스크 기준

```
KillSwitch 3-tier: 항상 활성 (bypass 불가)
  Tier1 (<1ms): halt 플래그 → 신규 주문 차단
  Tier2 (<500ms): 미체결 주문 취소
  Tier3 (<2000ms): 오픈 포지션 시장가 청산

CircuitBreaker: CLOSED→OPEN→HALF_OPEN (300s cooldown)
RiskGuardian: 11-check 매 거래 전 자동

손실 한도:
  단일 전략 > 5% → 전략 비활성
  총 손실 > 10% → KillSwitch
  5회 동일 문제 → 텔레그램 L2 에스컬레이션

Graceful shutdown: SIGTERM → 30s timeout → 미체결취소 → 포지션청산
close_positions.py: crash/SIGKILL 이후 fallback 전용 (정상 경로 아님)
```

**운영 금지 사항**:
- 카나리 실행 중 코드 수정 (모니터링만)
- 치명 버그 시 → graceful shutdown → Fix → 재시작
- Paper/Live 동시 실행 (Redis 글로벌 키 충돌)

---

## §7. 아키텍처 핵심

### 실행 파이프라인
```
WS Orderbook → SignalGenerator + RealSignalProducer
             → DeduplicationGate (asyncio.Lock per symbol+exchange_pair)
             → RiskGuardian 11-check
             → MarginTracker.reserve()
             → AtomicExecutor
             → 성공: MarginTracker.release()
             → 실패: StrandedPositionTracker (total>$30 시만 HALT)
```

### 멀티전략 동시 운영
- 자본: $120 공유 풀 (전략별 % 할당)
- 동시 체결: 글로벌 semaphore max=2 (선착순, 우선순위 없음)
- 신호 랭킹: Phase 3 기능 (현재 없음)
- **collision_key 버그**: strategy_id 미포함 → P1 수정 대기

### 모드
| Mode | Executor | 용도 |
|------|----------|------|
| live | AtomicExecutor | 실거래 |
| paper | PaperExecutor (BookWalkSlippage) | 시뮬 |
| backtest | SimExecutor | 과거 리플레이 |

> shadow 모드 없음. "shadow" 언급 금지.

### 설정 소스 (2개만)
- `engine/.env`: API 키/시크릿 전용
- `engine/config/engine.json`: 모든 운영 설정 (모드/자본/리스크/전략/거래소)
- `engine/config/strategy_params.json`: 전략별 임계값
- `engine/config/strategy_activation.json`: 활성/비활성 목록

---

## §8. 다음 실행 순서

### 즉시 (v94 진행중, 13:51 KST 기준)
1. FR 결산 3회 대기: UTC 16:00 Apr13 (1차), 00:00 Apr14 (2차), 08:00 Apr14 (3차)
2. collision_key: ✅ 수정완료 (v94 배포, 98 tests pass)
3. FF spread_exit: 1/3 확인 (13:27 KST), 2회 더 필요
4. BUG-74: ✅ 수정완료 (live.py:1115, 9 unit tests pass) — v95 재시작 시 활성
5. BUG-75: v95 재시작 시 자동 해결 (max_hold_seconds=300 적용)
6. BUG-76: ✅ 수정완료 (futures_futures.py:406,224 — adaptive p50 exit 제거, static 4.05bps 고정, 8 tests pass)

### v95 재시작 (UTC 08:00 = KST 17:00 FR 결산 후)
1. UTC 08:00 도달 → FR `_check_settlement_release()` 자동 4포지션 청산
2. FR 청산 확인 (`settlement_exit` 로그 4건) 후 v94 graceful shutdown (SIGTERM)
3. v95 시작 — 3버그 픽스 자동 활성:
   - BUG-74: margin guard ($3 미만 차단)
   - BUG-75: max_hold_seconds=300 (config 재로드)
   - BUG-76: static exit (4.05bps) — 즉시청산 방지
4. v95 목표: FF spread_exit 진짜 3회 (4.05bps 이하 수렴 시), FR 결산 3회
- **완료 조건**: crash=0 + KS=0 + FR결산 3회 + FF spread_exit 3회 + PnL≥0

### 운영 프롬프트 (자율 모드)
```
PHOENIX_PLAN.md §1~8 읽어. 이 문서가 유일한 실행 기준.

규칙:
1. §5 버그 최우선 (P0→P1 순서)
2. collision_key 수정 → pytest → v95 준비
3. 체결/크래시/KS 모니터링 — 문제 시 graceful shutdown → Fix → 재시작
4. 카나리 vN 종료 시 반드시 git commit + push + checkpoint save
5. PHOENIX_PLAN.md에 없는 작업 금지

금지: shadow 언급, TeamCreate, 증거없는 완료선언, scope 확장
보고: [vN] ✅/❌ + 로그증거 + 다음조치
```
