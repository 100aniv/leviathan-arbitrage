# TF QF 9차 -- 단계3 교차검증: 엔진 무결성 (Jeongyeon)

**검증일**: 2026-03-22
**검증자**: Jeongyeon (교차검증 에이전트)
**대상**: engine/src/main.py + 관련 모듈

---

## 1. 초기화 체인 -- PASS

`Engine.run()` (main.py:154-181)에서 11개 _init_* 메서드가 순차 호출됨을 확인.

| # | 메서드 | 호출 위치 | non-None 보장 | 판정 |
|---|--------|-----------|---------------|------|
| 1 | `_init_config()` | main.py:160 | `self._settings` = `get_settings()` 또는 `Settings()` fallback (main.py:372,381) | PASS |
| 2 | `_init_infrastructure()` | main.py:161 | `self._event_bus` = InMemoryEventBus 또는 Redis EventBus (main.py:449,458) | PASS |
| 3 | `_init_exchanges()` | main.py:162 | `self._exchanges` dict populated via `_init_paper_exchanges` (main.py:633-666) | PASS |
| 4 | `_init_signal_pipeline()` | main.py:163 | `self._signal_generator` = `SignalGenerator(...)` (main.py:837-849) | PASS |
| 5 | `_init_strategies()` | main.py:164 | `self._strategy_manager` = `StrategyManager(...)` (main.py:885-889) | PASS |
| 6 | `_init_risk()` | main.py:165 | `self._risk_guardian` = `RiskGuardian(...)` (main.py:1082-1084), `self._circuit_breaker` = `CircuitBreaker(...)` (main.py:1075) | PASS |
| 7 | `_init_execution()` | main.py:166 | `self._executor` = `AtomicExecutor(...)` (main.py:1159), `self._trade_consumer` = `TradeRequestConsumer(...)` (main.py:1168-1173) | PASS |
| 8 | `_populate_context()` | main.py:167 | context 필드 할당 (main.py:1566-1592) | PASS |
| 9 | `_startup_position_scan()` | main.py:168 | 조건부 실행 (Redis 없으면 skip, main.py:2649-2681) | PASS |
| 10 | `_startup_compliance_audit()` | main.py:169 | try/except 감싸여 실행 (main.py:2683-2693) | PASS |
| 11 | `_start_background_tasks()` | main.py:170 | tasks 리스트에 추가 후 `self.state.background_tasks.extend(tasks)` (main.py:1726) | PASS |

추가: `_init_tuner()` (main.py:171) -- 12번째 메서드, 선택적 (ENABLE_INLINE_TUNER 환경변수).

**결론: 11개 초기화 메서드 모두 순차 호출 확인. PASS**

---

## 2. 전략 등록 -- PASS

`_register_default_strategies()` (main.py:917-1019)에서 7개 전략 등록 확인.

| # | 전략 | 클래스 | 등록 위치 | 판정 |
|---|------|--------|-----------|------|
| 1 | cross_exchange | `CrossExchangeStrategy("cross_exchange_v1", ...)` | main.py:973-975 | PASS |
| 2 | spot_futures | `SpotFuturesStrategy("spot_futures_v1", ...)` | main.py:976-977 | PASS |
| 3 | futures_futures | `FuturesFuturesStrategy("futures_futures_v1", ...)` | main.py:978-979 | PASS |
| 4 | triangular | `TriangularStrategy("triangular_v1", ...)` | main.py:980-981 | PASS |
| 5 | funding_rate | `FundingRateStrategy("funding_rate_v1", ...)` | main.py:982-983 | PASS |
| 6 | statistical_arb | `StatisticalArbStrategy("statistical_arb_v1", ...)` | main.py:984-989 (조건부: tuned status "READY"/"MONITOR") | PASS |
| 7 | cex_dex | `CexDexStrategy("cex_dex_v1", ...)` | main.py:993-1012 (조건부: DEX_RPC_URL 또는 SHADOW_MOCK_DEX) | PASS |

등록 루프: `for strategy in strategies: self._strategy_manager.register(strategy)` (main.py:1014-1015)

**주의사항**: statistical_arb는 `tuned.get("statistical_arb", {}).get("status")` 조건부. strategy_params.json에 status가 없으면 미등록. cex_dex는 DEX_RPC_URL 또는 SHADOW_MOCK_DEX=true 필요. 이는 의도된 설계.

**결론: 7개 전략 모두 등록 경로 존재. PASS**

---

## 3. RiskGuardian -- PASS

### 3-1. Check 구현 (guardian.py)

| Check | 이름 | 구현 위치 | 판정 |
|-------|------|-----------|------|
| #0 | Halt check (kill switch) | guardian.py:154-167 (`is_halted()`) | PASS |
| #1 | Position limit | guardian.py:169-184 | PASS |
| #2 | Drawdown limit | guardian.py:186-197 | PASS |
| #3 | Exposure limit | guardian.py:200-213 | PASS |
| #4 | Circuit breaker state | guardian.py:215-222 | PASS |
| #4e | Net exposure per asset (Amendment 7) | guardian.py:224-245 | PASS |
| #5 | Exchange health score | guardian.py:247-266 | PASS |
| #6 | Max single trade size | guardian.py:268-282 | PASS |
| #7 | Volatility check | guardian.py:284-301 | PASS |
| #8 | Max rollback cost (Amendment 3C) | guardian.py:303-323 | PASS |
| #9 | Correlation scale-down (US-118/176/264) | guardian.py:325-348 | PASS |
| #10 | Max concurrent positions (US-154) | guardian.py:350-362 | PASS |
| #11 | Per-strategy capital allocation (US-196) | guardian.py:364-385 | PASS |
| #12 | Per-strategy circuit breaker (US-222/228) | guardian.py:387-399 | PASS |

실제로는 14개 체크 (0~12 + 4e). 문서에는 "9 pre-trade checks"라 되어있으나 Amendment/US 추가로 14개로 확장됨.

### 3-2. DataQualityManager 연결

- **DQM 초기화**: main.py:1114-1126 -- `DataQualityManager()` 생성 후 각 exchange `register_exchange()` 호출
- **always_healthy 적용**: main.py:1120-1121 -- `is_paper = isinstance(adapter, PaperExchangeAdapter)` -> `always_healthy=is_paper`
- **RiskGuardian 연결**: main.py:1122-1123 -- `self._risk_guardian.data_quality_manager = self._data_quality_manager`
- **Check #5에서 사용**: guardian.py:248-250 -- `if self.data_quality_manager is not None: _dqm_score = self.data_quality_manager.get_health_score(proposal.exchange_id)`

**always_healthy 수정 반영 확인**: data_quality_manager.py:184-195 -- `register_exchange(exchange_id, always_healthy=False)` 메서드에서 `always_healthy=True`면 `self._always_healthy.add(exchange_id)`, `get_health_score()`에서 `if exchange_id in self._always_healthy: return 1.0` (data_quality_manager.py:207-209)

**결론: Check #0~#12 모두 구현, DQM 연결 완료. PASS**

---

## 4. KillSwitch -- PASS

### 4-1. 구현 확인

- **모듈-레벨 halt flag**: kill_switch.py:33 -- `_HALT_FLAG = threading.Event()`
- **halt_local()**: kill_switch.py:36-55 -- `_HALT_FLAG.set()` + Rust AtomicBool 동기화
- **is_halted()**: kill_switch.py:75-92 -- Python flag OR Rust flag 체크
- **clear_halt()**: kill_switch.py:95-107

### 4-2. KillSwitchTarget 프로토콜

- **정의**: kill_switch.py:115-134 -- `Protocol` 클래스
  - `exchange_id` property
  - `cancel_all_orders(timeout_ms)` async method
  - `close_all_positions(timeout_ms)` async method

### 4-3. 3-Tier Kill Switch

- **KillSwitch 클래스**: kill_switch.py:162-378
  - **Tier 1** (<1ms): `_tier1_local_halt()` (kill_switch.py:227-255) -- `halt_local()` + Redis HALT key
  - **Tier 2** (<500ms): `_tier2_cancel_orders()` (kill_switch.py:257-317) -- `asyncio.gather` 병렬 cancel
  - **Tier 3** (<2000ms): `_tier3_close_positions()` (kill_switch.py:319-369) -- `asyncio.gather` 병렬 close

### 4-4. 5초 내 거래 중단

- Tier 1 (halt_local): <0.01ms -- `threading.Event.set()`
- 모든 주문 경로에 `is_halted()` 체크:
  - **TradeRequestConsumer**: trade_consumer.py:172 (consume loop) + trade_consumer.py:239 (per-message)
  - **RiskGuardian Check #0**: guardian.py:156 (`is_halted()`)
- Tier 1 완료 즉시 새 주문 불가. Tier 2+3 합산 <5s.
- **Shadow mode KillSwitch**: main.py:2337 -- `_shadow_kill_switch = _KillSwitch()`

### 4-5. _init_kill_switch() 별도 메서드 부재

main.py에 `_init_kill_switch()` 이름의 별도 메서드는 없음. KillSwitch는 다음 경로로 초기화:
- Shadow mode: main.py:2336-2337 -- `_shadow_kill_switch = _KillSwitch()`
- LiveGate 내부: main.py:2388 -- `kill_switch = KillSwitch()`
- RiskGuardian: `halt_local()` / `is_halted()` 모듈-레벨 함수 직접 사용 (guardian.py:30, 156)

별도 _init_kill_switch() 메서드가 아닌 모듈-레벨 함수 패턴으로 구현. 이는 의도된 설계 -- kill switch는 프로세스 전역 상태(threading.Event)이므로 별도 init 불필요.

**결론: 3-Tier KillSwitch 구현 완료, KillSwitchTarget 프로토콜 준수, 5초 내 거래 중단 가능. PASS**

---

## 5. Dead Wiring (TradeRequest -> Executor 경로) -- PASS

### 5-1. Signal -> Strategy -> TradeRequest 경로

**Paper/Synthetic mode**:
1. `_orderbook_feed_loop()` (main.py:1747-1789) -> `SignalGenerator.on_orderbook_update()` -> Signal 생성 -> EventBus publish
2. `_strategy_manager_loop()` (main.py:1729-1736) -> `StrategyManager.start()` -> EventBus subscribe -> `route_signal()` (manager.py:254) -> `strategy.on_signal()` -> TradeRequest 생성 -> EventBus publish
3. `_trade_consumer_loop()` (main.py:1738-1745) -> `TradeRequestConsumer.start()` -> EventBus subscribe

**Shadow mode**:
1. `_shadow_mode_loop()` (main.py:2308) -> ShadowMode orchestrator
2. ShadowMode._on_orderbook_update() -> SignalGenerator -> Signal
3. `_route_signal_to_strategies()` (shadow.py:1607-1622) -> `StrategyManager.route_signal()` -> Strategy.on_signal() -> TradeRequest
4. `_execute_shadow_trade_request()` -- PaperExecutor 직접 실행 (shadow mode 전용 경로)

**Live mode**:
1. `_live_mode_loop()` (main.py:1932) -> CollectorManager -> on_orderbook callback
2. SignalGenerator.on_orderbook_update() -> Signal -> EventBus
3. StrategyManager -> route_signal -> TradeRequest -> EventBus
4. TradeRequestConsumer -> `_process_message()` (trade_consumer.py:197) -> risk check -> `_execute()` (trade_consumer.py:296-337) -> AtomicExecutor

### 5-2. TradeRequestConsumer Execute 경로

trade_consumer.py:296-337:
- `len(orders) > 2 and len(exchange_ids) == 1` -> `executor.execute_multi_leg()` (executor.py:339)
- `len(exchange_ids) == 1` -> `executor.execute_same_exchange()` (executor.py:219)
- else -> `executor.execute_cross_exchange()` (executor.py:482)

### 5-3. 끊긴 곳 없음 확인

- Signal -> EventBus -> StrategyManager -> Strategy.on_signal() -> TradeRequest: **연결됨**
- TradeRequest -> EventBus -> TradeRequestConsumer -> risk_check -> AtomicExecutor: **연결됨**
- AtomicExecutor -> Exchange Adapter (execute/cancel): **연결됨**
- Shadow mode: 별도 `_execute_shadow_trade_request()` 경로로 PaperExecutor 직접 실행: **연결됨**

**결론: TradeRequest -> Executor 전달 경로에 끊긴 곳 없음. PASS**

---

## 6. LiveGate 6-check -- PASS

### 6-1. LiveGate (live_gate.py) 6개 체크

| # | 체크 | 구현 위치 | 임계치 | 판정 |
|---|------|-----------|--------|------|
| 1 | Sharpe >= 2.5 | live_gate.py:141-155 | `SHARPE_THRESHOLD = 2.5` (line 77) | PASS |
| 2 | MDD < 5% | live_gate.py:160-172 | `MDD_THRESHOLD = 0.05` (line 78) | PASS |
| 3 | Signals/Day >= 100 | live_gate.py:177-189 | `MIN_SIGNALS_PER_DAY = 100` (line 79) | PASS |
| 4 | KillSwitch not halted | live_gate.py:194-205 | `_check_kill_switch()` (line 415-430) | PASS |
| 5 | Circuit Breaker = CLOSED | live_gate.py:210-222 | `_get_circuit_breaker_state() == 0` (line 432-464) | PASS |
| 6 | Exchange Health >= 0.95 | live_gate.py:227-238 | `MIN_EXCHANGE_HEALTH = 0.95` (line 80), `_check_exchange_health()` (line 466+) | PASS |

### 6-2. ContinuousLiveGateMonitor (live_gate_continuous.py)

- 클래스 정의: live_gate_continuous.py:15-81
- `_evaluate()` (line 66-76): `self._live_gate.evaluate()` 호출 -> 실패시 `self._risk_guardian.trigger_halt("live_gate_failed")`
- Background task 등록: main.py:1712-1724 -- `self._live_gate.start_continuous_monitor()`

### 6-3. LiveGate 초기화

- Shadow mode: main.py:2385-2397 -- `LiveGate(pool, telegram, kill_switch, circuit_breaker, exchange_health_fn, settings)`
- Progressive shadow: main.py:2567-2579 -- 동일 패턴
- DQM health scores 연결: main.py:2390 -- `_ehf = self._data_quality_manager.get_all_health_scores`

**결론: LiveGate 6개 체크 모두 구현 확인. PASS**

---

## 종합 판정

| # | 검증 항목 | 판정 | 비고 |
|---|-----------|------|------|
| 1 | 초기화 체인 11개 | **PASS** | 모든 _init_* 순차 호출, non-None 보장 (fallback 포함) |
| 2 | 전략 등록 7개 | **PASS** | 7개 전략 등록 경로 확인 (stat_arb/cex_dex 조건부는 의도된 설계) |
| 3 | RiskGuardian 14-check + DQM | **PASS** | Check #0~#12 구현, DQM always_healthy 수정 반영 |
| 4 | KillSwitch 3-Tier | **PASS** | threading.Event + Rust + Redis, KillSwitchTarget 프로토콜, <5s 중단 |
| 5 | Dead Wiring | **PASS** | Signal -> Strategy -> TradeRequest -> Executor 전 경로 연결 확인 |
| 6 | LiveGate 6-check | **PASS** | Sharpe/MDD/Signals/KillSwitch/CB/Health 모두 구현 |

**최종 판정: ALL 6 PASS -- 엔진 무결성 검증 통과**
