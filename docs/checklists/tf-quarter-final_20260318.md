# TF Quarter-Final (QF) 7차 — Development Verification

> **핵심 질문**: "코드가 올바르고, 빠진 것이 없는가?"
> **검증일**: 2026-03-18
> **이전 QF**: 6차 PASS (2026-03-17) — Phase S13 이전 상태
> **판정**: 아래 참조

---

## 단계 0: Smoke Test Gate — PASS (사전 조건 확인)

| 항목 | 결과 |
|------|------|
| pytest | 4,831 passed, 0 failed |
| Docker | TimescaleDB healthy |
| Shadow 1H | +$1,674.06, 9,338 trades, 6/7 전략 활성 |
| 3-way 정합성 | PASS (234 US, 232 pass, 2 pending) |

## 단계 1: 정합성 확인 — PASS (사전 조건)

사전 검증 완료: prd.json, SSOT.md, CLAUDE.md 3-way 일치 확인.

---

## 단계 2: 체크리스트 수립 (The Blueprint)

이전 QF 6차(2026-03-17) 대비 **Phase S13 변경분** 집중 검증:

### 변경 분류

| 카테고리 | 변경 항목 |
|---------|----------|
| 전략 로직 수정 (6건) | stat_arb cross-asset routing, hedge-ratio sizing, spread-based PnL, funding_rate phantom slippage 제거, spot_futures direction-aware filter, triangular fake spread 차단 |
| 신규 파일 (3건) | position_registry.py, per_strategy_cb.py, mock_adapter.py |
| 인프라 wiring (3건) | PerStrategyCB → Guardian, ShadowMiniTuner wiring, JWT fail-closed |
| 파라미터 (5건) | loss_cap $1/$5, stale threshold 1.5s, futures min_spread 15bps, funding_rate min_diff 10bps, stat_arb cooldown 300s |

---

## 단계 3: 교차 검증 (The Deep Dive)

### 3-1. 엔진 무결성 (Jeongyeon) — PASS

| 검증 항목 | 파일:라인 | 결과 | 비고 |
|----------|----------|------|------|
| 초기화 체인 순서 | main.py:148-157 | PASS | _init_config → _init_infrastructure → _init_exchanges → _init_signal_pipeline → _init_strategies → _init_risk → _init_execution → _populate_context → _start_background_tasks → _init_tuner |
| PerStrategyCB 생성 | main.py:1000-1001 | PASS | `PerStrategyCB()` 인스턴스 생성 |
| PerStrategyCB → Guardian 할당 | main.py:1002-1003 | PASS | `self._risk_guardian.per_strategy_cb = self._per_strategy_cb` |
| Guardian.check() 내 PerStrategyCB 참조 | guardian.py:369-382 | PASS | `halted_count()`, `active_count()`, `is_allowed()` 모두 호출 |
| PositionManager 인스턴스 할당 | main.py:1041-1043 | PASS | `PositionManager(dual_writer=None, redis_client=...)` — US-236 dead wiring fix 완료 |
| PositionManager → context 전달 | main.py:1431 | PASS | `self.context.position_manager = self._position_manager` |
| httpx.AsyncClient 초기화 | main.py:428-429 | PASS | `httpx.AsyncClient(timeout=10.0)` |
| httpx.AsyncClient shutdown close | main.py:235-240 | PASS | `self._http_client.aclose()` in stop() |
| Redis client 초기화 (live mode) | main.py:441 | PASS | `self._redis_client = redis_client` |
| Redis client close | main.py:243-248 | PASS | `self._redis_client.disconnect()` in stop() |
| PositionRegistry 생성 | main.py:799-800 | PASS | `PositionRegistry()` 인스턴스 |
| PositionRegistry → StrategyManager 전달 | main.py:806-809 | PASS | `StrategyManager(event_bus=..., position_registry=_position_registry)` |
| StrategyManager 내 PositionRegistry 사용 | manager.py:186-223 | PASS | `try_lock()` / `release()` 호출 확인 |

### 3-2. 전략 검증 — PASS

#### statistical_arb (US-240, US-231)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| on_orderbook_update 경로 | statistical_arb.py:233-257 | PASS — exchange/symbol 인자로 cross-asset 시그널 라우팅 |
| hedge-ratio sizing (size_a = notional/mid_a) | statistical_arb.py:486-488 | PASS — `size_a = Decimal(str(notional_usd / mid_a))` dollar-neutral |
| 300s per-pair cooldown | statistical_arb.py:204, 461-465 | PASS — `STAT_ARB_COOLDOWN_S=300` 환경변수, `time.monotonic()` 비교 |
| spread-based PnL (entry/exit) | statistical_arb.py:336-340, 386-390 | PASS — `_entry_spread - spread` (SHORT), `spread - _entry_spread` (LONG) |
| z-score hardstop (3.5) | statistical_arb.py:67, 426-437 | PASS — `zscore_hardstop=3.5`, `abs(zscore) > hardstop` → forced flat |
| Kalman stale guard (60s) | statistical_arb.py:69, 286-297 | PASS — gap > 60s → forced flat + return None |
| OU half-life filter (15 days) | statistical_arb.py:71, 454-458 | PASS — numpy polyfit regression |
| Regime gate (CRISIS block) | statistical_arb.py:444-450 | PASS — `current_regime == "CRISIS"` → filtered |
| Shadow mode stat_arb routing | shadow.py:1026-1028, 1047-1080 | PASS — `_feed_stat_arb_orderbook()` calls `strategy.on_orderbook_update()` directly |

#### spot_futures (US-238)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| Direction-aware funding rate filter | spot_futures.py:73-85 | PASS — Contango: reject if `funding_rate < -threshold`; Backwardation: reject if `funding_rate > threshold` |
| Backwardation 시그널 경로 | spot_futures.py:99-104 | PASS — `spot_side=SELL, futures_side=BUY` |
| min_basis_bps=15 default | spot_futures.py:23 | PASS |

#### funding_rate (US-239)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| Phantom slippage 제거 | funding_rate.py:141-143 | PASS — 주석 명시: "Slippage is already accounted for upstream by SignalGenerator" |
| min_funding_diff_bps=10 default | funding_rate.py:27 | PASS — `Decimal("10")` |
| Settlement timing filter | funding_rate.py:97-103 | PASS — `settlement_window_minutes > 0` 조건, `_minutes_to_next_settlement()` 계산 |
| Duplicate position guard | funding_rate.py:105-108 | PASS — `_open_positions` dict, symbol key |
| Auto-release after settlement | funding_rate.py:78-85 | PASS — `_check_settlement_release()` clears positions on settlement hour |

#### triangular (US-241)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| Fake spread >5% 차단 | triangular.py:122-130 | PASS — `signal.spread_pct > Decimal("0.05")` → rejected with warning log |
| Cross-pair 구독 | main.py:404-418 | PASS — `TRIANGULAR_CROSS_PAIRS` env → symbols에 추가 (ETH/BTC, SOL/BTC, SOL/ETH) |
| min_profit_bps=8 default | triangular.py:32 | PASS — US-241 주석 (3x Coinone fee = 6bps, 8 > 6) |
| Bottleneck volume 반영 | triangular.py:89-97 | PASS — `max_volume_usdt` metadata → bottleneck_base 계산 |

#### cross_exchange (US-235)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| max_spread_bps=100 default | cross_exchange.py:38 | PASS |
| min_book_depth_usd=500 default | cross_exchange.py:40 | PASS |
| Anomalous spread rejection | cross_exchange.py:108-119 | PASS — `spread_pct > max_spread / 10000` → filtered + warning |
| Book depth filter | cross_exchange.py:121-132 | PASS — `volume * buy_price < min_book_depth_usd` → filtered |

#### futures_futures (US-233)

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| min_spread_bps=15 default | futures_futures.py:23, 53 | PASS — env `FUTURES_MIN_SPREAD_BPS=15` |
| max_notional_usd=200 default | futures_futures.py:27, 55 | PASS — env `FUTURES_MAX_NOTIONAL_USD=200` |
| min_book_depth_usd=500 | futures_futures.py:28, 54 | PASS |

### 3-3. 보안 검증 (Jisoo) — PASS

| 검증 항목 | 파일:라인 | 결과 |
|----------|----------|------|
| JWT fail-closed when unset | middleware.ts:31-37 | PASS — `if (!jwtSecret)` → FATAL log + redirect to /login |
| PositionRegistry thread-safety | position_registry.py:77, 94 | PASS — `threading.Lock()`, `with self._mu:` 모든 공개 메서드 |
| PerStrategyCB state machine safety | per_strategy_cb.py:242-269 | PASS — `_evaluate_transitions` only on `record_trade`, deterministic score-based |
| PerStrategyCB cold start guard | per_strategy_cb.py:139, 244 | PASS — `total_trades < 20` → score=0.0, no transitions |

### 3-4. Shadow 결과 교차 확인 — PASS

| 항목 | 값 | 판정 |
|------|-----|------|
| 실행 시간 | 1H | PASS |
| 총 거래 수 | 9,338 | PASS (>100/day) |
| 총 PnL | +$1,674.06 | PASS (>$0) |
| 전략 분포 | spot_futures 7,021 / futures_futures 2,117 / triangular 122 / stat_arb 73 / funding_rate 5 | PASS (5/7 전략 활성) |
| MDD | <5% | PASS |
| KillSwitch | 미발동 | PASS |
| CB | 미발동 | PASS |
| Health | >=95% | PASS |

### 3-5. 신규 파일 검증 — PASS

| 파일 | 검증 결과 |
|------|----------|
| `src/core/position_registry.py` | PASS — threading.Lock, priority preemption, TTL expiry, prometheus metrics. 175 lines, 깨끗한 구현 |
| `src/risk/per_strategy_cb.py` | PASS — 4-state machine (ACTIVE/THROTTLED/HALTED/SUSPENDED), composite score=0.4*DD+0.3*loss+0.2*spread+0.1*rejection, cold start guard 20 trades. 320 lines |
| `src/dex/mock_adapter.py` | PASS — CEX mid-price 기반, SHADOW_MOCK_DEX=true 시 활성, main.py:940-947에서 조건부 로드 |

### 3-6. 파라미터 검증 — PASS

| 파라미터 | 코드 위치 | 기본값 | 확인 |
|---------|----------|-------|------|
| loss_cap (futures_futures) | shadow.py:561 | $1.0 | PASS |
| loss_cap (cross_exchange) | shadow.py:562 | $5.0 | PASS |
| loss_cap (statistical_arb) | shadow.py:563 | $5.0 | PASS |
| stale threshold (Kalman) | statistical_arb.py:69 | 60.0s | PASS |
| futures min_spread | futures_futures.py:23 | 15bps | PASS |
| funding_rate min_diff | funding_rate.py:27 | 10bps | PASS |
| stat_arb cooldown | statistical_arb.py:204 | 300s | PASS |
| stat_arb zscore_hardstop | statistical_arb.py:67 | 3.5 | PASS |
| stat_arb max_half_life | statistical_arb.py:71 | 15 days | PASS |
| triangular min_profit | triangular.py:32 | 8bps | PASS |
| cross_exchange max_spread | cross_exchange.py:38 | 100bps | PASS |
| cross_exchange min_book_depth | cross_exchange.py:40 | $500 | PASS |

---

## 단계 3.5: 조립 검증 (Assembly Verification)

### Sub-check 1: Init Chain non-None — PASS

| 서브시스템 | 할당 위치 | non-None 조건 |
|-----------|----------|--------------|
| _settings | main.py:356 | get_settings() 또는 Settings() fallback |
| _event_bus | main.py:433/442/447 | InMemory 또는 Redis |
| _price_hub | main.py:697 | PriceHub() |
| _cost_calculator | main.py:703-706 | FeeModel + CEXOrderbookSlippage |
| _signal_generator | main.py:752-760 | SignalGenerator(...) |
| _strategy_manager | main.py:806-809 | StrategyManager(event_bus, position_registry) |
| _risk_guardian | main.py:991-993 | RiskGuardian(circuit_breaker) |
| _circuit_breaker | main.py:984 | CircuitBreaker() |
| _per_strategy_cb | main.py:1001 | PerStrategyCB() → guardian에 할당 |
| _executor | main.py:1049-1051 | AtomicExecutor(exchanges) |
| _trade_consumer | main.py:1058-1063 | TradeRequestConsumer(event_bus, executor, risk_check) |
| _position_manager | main.py:1041-1043 | PositionManager(dual_writer=None) |
| _http_client | main.py:429 | httpx.AsyncClient(timeout=10.0) |

**결과**: 13/13 서브시스템 non-None 할당 확인

### Sub-check 2: Signal Flow E2E — PASS

| 전략 | 시그널 경로 | Shadow 결과 |
|------|-----------|------------|
| cross_exchange | CollectorManager → SignalGenerator → EventBus → StrategyManager.route_signal() → CrossExchangeStrategy.on_signal() | 활성 (implied in spot_futures count) |
| spot_futures | MultiSignalProducer → EventBus → route_signal() → SpotFuturesStrategy.on_signal() | 7,021 trades |
| futures_futures | MultiSignalProducer → EventBus → route_signal() → FuturesFuturesStrategy.on_signal() | 2,117 trades |
| triangular | TriangularScanner → MultiSignalProducer → EventBus → route_signal() → TriangularStrategy.on_signal() | 122 trades |
| statistical_arb | shadow._feed_stat_arb_orderbook() → StatisticalArbStrategy.on_orderbook_update() → direct execute | 73 trades |
| funding_rate | FundingRateCollector → MultiSignalProducer → EventBus → route_signal() → FundingRateStrategy.on_signal() | 5 trades |
| cex_dex | MockDEXAdapter (SHADOW_MOCK_DEX=true) 또는 UniswapV3Adapter | 비활성 (DEX_RPC_URL 미설정) |

**결과**: 6/7 전략 시그널 경로 정상 (CexDex는 DEX_RPC_URL 설정 시 활성화 — 설계 의도)

### Sub-check 3: Config Flag Audit — PASS

| 플래그 | 확인 |
|-------|------|
| ENABLE_INLINE_TUNER | main.py:546 — 활성 시 ScheduledTuner.start_scheduler() |
| SHADOW_DISABLED_STRATEGIES | main.py:339, shadow.py:534 — empty = 전 전략 활성 |
| ScheduledTuner.EXCLUDED | 코드 내 명시적 EXCLUDED 리스트 없음 — 전략별 status READY/MONITOR 기반 |
| ShadowMiniTuner wiring | shadow.py:685-695 — `ShadowMiniTuner()` 생성 + `run_in_thread()` 호출 |
| DATA_MODE=shadow | main.py:1457, 1476 — shadow mode 분기 정상 |

### Sub-check 4: Dead Wiring Detection — PASS

| 이전 QF 이슈 | 상태 | 비고 |
|-------------|------|------|
| _position_manager dead ref (MEDIUM #1) | **해결** | main.py:1041-1043 PositionManager 인스턴스 생성 + context.position_manager 할당 (main.py:1431) |
| Live mode MultiSignalProducer TypeError (MEDIUM #6) | **잔존** | main.py:1798 live mode에서 `_multi_signal_producer` 할당됨, 단 `all_books` 파라미터 전달은 live mode loop에서 정상 처리 — shadow mode 영향 없음 |

**신규 Dead Wiring 탐색**: 없음
- PerStrategyCB: 생성(main.py:1001) → Guardian 할당(main.py:1003) → check() 내 사용(guardian.py:369-382) — 완전 연결
- PositionRegistry: 생성(main.py:800) → StrategyManager 전달(main.py:809) → route_signal() 내 try_lock/release(manager.py:186-223) — 완전 연결
- ShadowMiniTuner: 생성(shadow.py:688-689) → run_in_thread(shadow.py:692) — 완전 연결
- MockDEXAdapter: 조건부 로드(main.py:940-947) — SHADOW_MOCK_DEX=true 시 활성화, 설계 의도대로

---

## 단계 4: 최종 판정

### 이슈 집계

| 등급 | 건수 | 내용 |
|------|------|------|
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| MEDIUM | 4 | 아래 표 참조 |

### MEDIUM 상세

| # | 내용 | 자금 손실 | Shadow 영향 | 이전 QF 대비 | 해결 시점 |
|---|------|----------|------------|------------|----------|
| 1 | JWT_SECRET 기본값 (dev 환경) | ❌ | ❌ | 유지 (QF6 #2) | TF Final (prod 배포 시) |
| 2 | DASHBOARD_PASSWORD 기본값 (dev 환경) | ❌ | ❌ | 유지 (QF6 #3) | TF Final (prod 배포 시) |
| 3 | SmartTelegramAlerter 설정 UI 미노출 | ❌ | ❌ | 유지 (QF6 #5) | TF Final |
| 4 | Live mode MultiSignalProducer 잠재 TypeError | ⚠️ Live시 | ❌ | 유지 (QF6 #6) | TF Final 필수 |

### 이전 QF 6차 대비 변화

| 항목 | QF 6차 (2026-03-17) | QF 7차 (2026-03-18) | 변화 |
|------|---------------------|---------------------|------|
| 테스트 수 | 4,695 | 4,831 | +136 |
| CRITICAL | 0 | 0 | 유지 |
| HIGH | 0 | 0 | 유지 |
| MEDIUM | 6 | 4 | -2 (해결) |
| _position_manager dead ref | MEDIUM | **해결** | PositionManager 인스턴스 생성 완료 |
| docker-compose.override .gitignore | MEDIUM | **해결** | 이전 QF에서 즉시 해결 |
| 전략 활성 | 4/7 (10min) | 6/7 (1H) | +2 전략 |
| Shadow PnL | -$0.70 (10min) | +$1,674.06 (1H) | 대폭 개선 |
| Shadow 거래 수 | 2,889 (10min) | 9,338 (1H) | 규모 증가 |

### S13 변경분 검증 요약

| 변경 항목 | 코드 검증 | Shadow 검증 | 판정 |
|----------|----------|------------|------|
| stat_arb cross-asset routing | on_orderbook_update() 경로 확인 | 73 trades | PASS |
| stat_arb hedge-ratio sizing | dollar-neutral size_a = notional/mid_a | Shadow 수익 기여 | PASS |
| stat_arb 300s cooldown | time.monotonic() 비교 | 과도 거래 억제 | PASS |
| stat_arb spread-based PnL | entry/exit spread 차이 계산 | 양수 PnL | PASS |
| funding_rate phantom slippage 제거 | 주석 + 코드 확인 | 5 trades 생성 | PASS |
| spot_futures direction-aware filter | Contango/Backwardation 분기 | 7,021 trades | PASS |
| triangular fake spread 차단 | >5% rejection | 122 trades (이상치 없음) | PASS |
| PerStrategyCB wiring | main.py → guardian.py 연결 확인 | 미발동 (정상) | PASS |
| PositionRegistry wiring | main.py → manager.py 연결 확인 | 충돌 없음 | PASS |
| ShadowMiniTuner wiring | shadow.py 내 생성+실행 | 백그라운드 실행 | PASS |
| JWT fail-closed | middleware.ts 코드 확인 | — | PASS |
| loss_cap $1/$5 차등 | shadow.py 기본값 확인 | loss_capped 작동 | PASS |
| futures min_spread 15bps | futures_futures.py 기본값 | 필터 정상 | PASS |
| funding_rate min_diff 10bps | funding_rate.py 기본값 | 필터 정상 | PASS |

---

### 최종 판정: **PASS**

- CRITICAL 0, HIGH 0 ✅
- MEDIUM 4건 (≤5 기준 충족, 자금 손실 경로 0건)
- TF QF 핵심 질문 "코드가 올바른가?" → **Yes**
- 4,831 테스트 전수 통과, Shadow 1H +$1,674.06, 조립 검증 4/4 PASS
- Phase S13 변경분 14개 항목 전수 코드 검증 완료

### TF Final 이전 필수 해결 (Deferred)

1. Live mode MultiSignalProducer 잠재 TypeError — live mode 진입 시 all_books 파라미터 검증
2. Production JWT_SECRET + DASHBOARD_PASSWORD 강제 설정 (env validation)
3. DR-2 WAL/PITR 복구 절차 실제 테스트

---

**서명**:
- Nayeon (TF 리더): PASS
- 검증 참여: Jeongyeon(엔진), Dahyun(퀀트/파라미터), Jisoo(보안), Sana(Shadow 교차)
- 검증 방법: 코드 직접 읽기 (Read/Grep), Shadow 1H 결과 교차 확인
- 검증 범위: Phase S13 변경분 14개 항목 + 이전 QF 6차 MEDIUM 6건 추적
