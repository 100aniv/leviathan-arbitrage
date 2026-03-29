# Phase S17 PLAN — 전략별 고급 기능 + 실행 안전장치

**Phase**: S17
**US 범위**: US-266 ~ US-276 (12 US)
**날짜**: 2026-03-20
**복잡도**: MEDIUM-HIGH (7개 전략 파일 + 실행 레이어 변경)

---

## 1. Context

Phase S16까지 동적 임계치와 적응형 파라미터가 적용되었으나, 각 전략의 고급 기능(Bellman-Ford 비용 통합, OU 프로세스, Stale Guard 등)과 실행 안전장치(Partial Fill 손절, Depth 기반 사이징)가 미구현 상태. TF SF 9H 중단 시 지적된 "전략 평가 기준 위반" 항목의 근본 해결.

---

## 2. Work Objectives

1. **triangular**: Bellman-Ford 그래프에 Gas+Depth 비용 통합 + 3-leg 500ms 레이턴시 예산
2. **funding_rate**: OU 프로세스 기반 펀딩레이트 예측 + 다중 거래소 스캐너 (Bybit/OKX Futures 추가)
3. **spot_futures**: OU Basis 모델링 + max_holding_hours 강제 청산
4. **futures_futures**: Funding Convergence 합산 시그널 + Stale Guard
5. **stat_arb**: Z-score 거래비용 조정 (왕복 비용 > 예상수익 시 스킵)
6. **atomic**: Partial Fill 손절 + DepthAnalyzer 사이징 연결
7. **통합 Shadow 10min 검증**

---

## 3. Guardrails

### Must Have
- 모든 신규 기능에 env var 기반 ON/OFF 토글
- 기존 테스트 전량 통과 (regression 0)
- 각 US별 최소 3개 단위테스트
- WIRING AC 3개 (생성 -> 주입 -> 호출) 검증

### Must NOT Have
- 기존 전략 로직 시그니처 변경 (backward compat 유지)
- 하드코딩된 매직넘버 (env var 또는 Config 필드로)
- OU 프로세스에 numpy 외 중량 의존성 추가
- PaperExecutor에 슬리피지 이중 적용

---

## 4. Batch Groups (체크포인트)

### B1_independent (병렬, 서로 무관)
| US | 전략 | 예상 변경량 |
|----|------|------------|
| US-272 | futures_futures | ~40 LOC |
| US-273 | futures_futures | ~25 LOC |
| US-274 | stat_arb | ~20 LOC |

### B2_parallel_pairs (쌍 내 순차, 쌍 간 병렬)
| US 쌍 | 전략 | 예상 변경량 |
|--------|------|------------|
| US-266 + US-267 | triangular | ~80 LOC |
| US-270 + US-271 | spot_futures | ~70 LOC |
| US-275 + US-275-a | atomic | ~60 LOC |

### B3_sequential (순차)
| US | 전략 | 예상 변경량 |
|----|------|------------|
| US-268 (먼저) | funding_rate | ~60 LOC |
| US-269 (이후) | funding_rate + multi_signal | ~50 LOC |

### B4_final_gate (모든 US 완료 후)
| US | 내용 |
|----|------|
| US-276 | Shadow 10min 통합 검증 |

---

## 5. Detailed TODOs

### US-266: Bellman-Ford + 비용 통합 (triangular)

**파일**: `engine/src/core/triangular_scanner.py`

**현재 상태**: `_build_graph()` (L94~L133)에서 edge weight = `-log(rate)` 만 사용. Gas/Depth 비용 미반영.

**구현**:
1. `_build_graph()` 시그니처에 `cost_calculator: CostCalculator | None = None` 파라미터 추가
2. Edge weight 계산 변경: `weight = -log(rate) + log(1 + fee_rate + gas_cost/notional)`
   - `fee_rate`: cost_calculator.estimate_cost() / notional
   - `gas_cost`: 삼각 차익은 동일 거래소이므로 gas=0 (intra-exchange)
   - **[MUST FIX #7]** depth penalty: `_depth_bottleneck_usdt()` 결과가 min_volume_usdt 미만이면 **edge 제거 (prune)**, soft penalty가 아닌 hard prune. 유동성 부족한 경로는 완전 차단.
3. `TriangularScanner.__init__`에 `cost_calculator` 저장
4. `on_orderbook_update()`에서 `_build_graph(exchange_id)` 호출 시 cost 전달

**AC**:
- [ ] _build_graph()가 fee를 edge weight에 반영
- [ ] min_volume_usdt 미만 depth일 때 cycle 필터링
- [ ] 기존 테스트 + 비용 통합 테스트 3개 PASS

**WIRING AC**:
- [ ] 생성: `_build_graph()`에 cost 로직 추가
- [ ] 주입: `TriangularScanner.__init__(cost_calculator=...)` 호출부 (signal producer)
- [ ] 호출: `on_orderbook_update()` -> `_build_graph()` 경로에서 cost 적용 확인

---

### US-267: 삼각 차익 Latency Budget 500ms

**파일**: `engine/src/strategies/triangular.py`

**현재 상태**: `on_signal()` (L58~L192)에서 시간 제약 없음.

**구현**:
1. `TriangularConfig`에 `max_latency_ms: float = Field(default=500.0, gt=0)` 추가
2. `on_signal()`에서 signal.metadata에 `signal_timestamp_ms` 추가 확인
3. 진입 시점에 `elapsed_ms = now_ms - signal_timestamp_ms` 계산
4. `elapsed_ms > max_latency_ms` 이면 필터링 + 메트릭 기록
5. TradeRequest.metadata에 `"latency_ms"` 기록

**AC**:
- [ ] signal_timestamp_ms 기준 500ms 초과 시 None 반환
- [ ] env var `TRIANGULAR_MAX_LATENCY_MS` 로 오버라이드 가능
- [ ] 레이턴시 초과 필터 테스트 PASS

---

### US-268: OU Process Funding Rate 예측

**파일**: `engine/src/strategies/funding_rate.py` (신규 헬퍼: `engine/src/core/ou_process.py`)

**현재 상태**: `_funding_diff_history` deque (L72)에 z-score만 계산. OU 파라미터(mu, theta, sigma) 미추출.

**구현**:
1. **신규 파일** `engine/src/core/ou_process.py`:
   ```python
   class OUProcess:
       def __init__(self, window: int = 360):
           self._history: deque[tuple[float, float]] = deque(maxlen=window)  # (timestamp_s, value)

       def update(self, value: float, timestamp_s: float) -> None: ...

       @property
       def half_life(self) -> float:
           """OU half-life = -ln(2)/theta. inf if not mean-reverting."""

       @property
       def mu(self) -> float: """Long-run mean."""

       @property
       def theta(self) -> float: """Mean-reversion speed."""

       def predict(self, horizon_s: float) -> float:
           """E[X(t+h)] = mu + (X(t) - mu) * exp(-theta * h)"""
   ```
2. **[MUST FIX #1]** 시간 가중 OLS: event-driven 데이터는 등간격이 아님 → `dt[i] = t[i] - t[i-1]` 사용.
   `dX[i] = a*dt[i] + b*X[i-1]*dt[i]` 가중 선형회귀 (numpy lstsq). dt=0 방지: min_dt=0.1s.
3. `FundingRateStrategy.__init__`에 `self._ou = OUProcess(window=360)` 추가
4. `on_signal()`에서 `self._ou.update(funding_diff_bps, time.monotonic())` 호출 (timestamp 필수)
5. `half_life < FUNDING_OU_MIN_HALFLIFE_S` (env, default=300) 이면 필터 (너무 빠른 회귀 = 수익 기간 부족)
6. **[MUST FIX #8]** env var `ENABLE_OU_FILTER=true` (기본 ON, false로 비활성화 가능)

**AC**:
- [ ] OUProcess.half_life 정확도 테스트 (합성 데이터, 오차 < 20%)
- [ ] half_life < min 이면 on_signal() 필터링
- [ ] predict() 값이 수학적으로 정확 (E[X(t+h)] 공식)

---

### US-269: Funding Rate 다중 거래소 스캐너

**파일**: `engine/src/core/multi_signal.py` (L83 부근)

**현재 상태**: `_funding_rates: dict[str, dict[str, float]]` (exchange -> symbol -> rate) 존재하나 binance_futures만 활용.

**구현**:
1. `multi_signal.py`의 funding rate 수집부에 bybit_futures, okx_futures 추가
2. 기존 어댑터(Bybit, OKX)의 futures funding rate API 호출 경로 확인 및 연결
3. **[MUST FIX #6]** Funding rate settlement period 정규화: 거래소마다 settlement 주기 다름 (Binance 8h, Bybit 8h, OKX 8h).
   비교 전 반드시 연율화(annualized) 또는 동일 주기(8h)로 정규화. `rate_normalized = rate * (8 / settlement_hours)`
4. N개 거래소 중 (max_rate_exchange, min_rate_exchange) 쌍 선택 → 최대 diff 시그널 생성
5. env var `FUNDING_SCANNER_EXCHANGES` (default: `"binance_futures,bybit_futures,okx_futures"`)
6. **[MUST FIX #8]** env var `ENABLE_MULTI_FUNDING_SCANNER=true`

**AC**:
- [ ] 3개 거래소 funding rate 수집 확인 (로그)
- [ ] max-min 쌍 시그널 생성 테스트
- [ ] US-268의 OU 필터가 다중 거래소 시그널에도 적용

**WIRING AC**:
- [ ] 생성: multi_signal.py에 bybit/okx funding rate 수집 로직
- [ ] 주입: 어댑터 funding rate API -> multi_signal 경로
- [ ] 호출: Shadow 실행 시 3개 거래소 funding rate 로그 출력

---

### US-270: spot_futures OU Basis Modeling

**파일**: `engine/src/strategies/spot_futures.py`

**현재 상태**: `on_signal()` (L67~L197)에서 basis_bps 단순 비교. OU 기반 mean-reversion 필터 없음.

**구현**:
1. `SpotFuturesStrategy.__init__`에 `self._ou_basis = OUProcess(window=1440)` (US-268 재사용)
2. `on_signal()`에서 `self._ou_basis.update(float(abs_basis_bps))` 호출
3. **[MUST FIX #2]** `self._ou_basis.update(float(basis_bps), time.monotonic())` — abs() 제거! basis의 부호가 mean-reversion 방향 정보를 담음. abs()하면 contango/backwardation 구분 불가.
4. `ou_basis.half_life > MAX_BASIS_HALFLIFE_H` (env, default=24.0 hours) 이면 필터
5. 예측값 `ou_basis.predict(horizon_s=3600)` 이 현재값보다 mean 쪽이면 진입 신뢰도 boost
6. **[MUST FIX #8]** env var `ENABLE_BASIS_OU_FILTER=true`

**AC**:
- [ ] OU half_life 기반 필터 동작
- [ ] half_life > 24H 일 때 on_signal() None 반환
- [ ] OUProcess 재사용 확인 (코드 중복 없음)

---

### US-271: spot_futures max_holding_hours 강제

**파일**: `engine/src/strategies/spot_futures.py`

**현재 상태**: `SpotFuturesConfig.max_holding_hours=8.0` (L25) 정의만 있고 사용처 0. Dead field.

**구현**:
1. **[MUST FIX #3]** `SpotFuturesStrategy.__init__`에 full position state 추적:
   ```python
   @dataclass
   class OpenPosition:
       symbol: str
       entry_time: float  # monotonic
       entry_price: Decimal
       size: Decimal
       side: str  # "contango" or "backwardation"
       exchange_id: str

   self._open_positions: dict[str, OpenPosition] = {}
   ```
2. `on_signal()` 진입 시 `self._open_positions[signal.symbol] = OpenPosition(...)` 기록
3. `on_signal()` 상단에 만료 체크 — 풀 포지션 상태로 올바른 closing TradeRequest 생성:
   ```python
   now = time.monotonic()
   expired = [p for p in self._open_positions.values()
              if (now - p.entry_time) / 3600 > self.config.max_holding_hours]
   for pos in expired:
       # emit closing TradeRequest with correct side, price, exchange
       del self._open_positions[pos.symbol]
   ```
4. `on_fill()` 에서 exit trade 수신 시 `_open_positions` 제거
5. **[MUST FIX #8]** env var `ENABLE_HOLDING_TIMEOUT=true`

**AC**:
- [ ] 8시간 초과 포지션 강제 청산 TradeRequest 생성
- [ ] on_fill() exit 시 _open_positions 정리
- [ ] 시간 기반 강제 청산 테스트 PASS

**WIRING AC**:
- [ ] 생성: _open_positions dict + 만료 로직
- [ ] 주입: on_signal() 상단에서 만료 체크
- [ ] 호출: Shadow에서 max_holding_hours 경과 시 청산 로그

---

### US-272: futures_futures Funding Convergence

**파일**: `engine/src/strategies/futures_futures.py`

**현재 상태**: `on_signal()` (L74~L185)에서 spread_pct만 사용. Funding diff 미합산.

**구현**:
1. `FuturesFuturesConfig`에 `funding_convergence_weight: Decimal = Field(default=Decimal("0.3"), ge=0, le=1)` 추가
2. `on_signal()`에서 `funding_diff_bps = signal.metadata.get("funding_diff_bps", 0)` 추출
3. 합산 시그널: `combined_score = spread_bps + funding_convergence_weight * funding_diff_bps`
4. `combined_score < min_spread_bps` 이면 필터

**AC**:
- [ ] spread + funding diff 합산 동작
- [ ] funding_convergence_weight=0 일 때 기존 동작과 동일 (backward compat)
- [ ] 합산 시그널 테스트 PASS

---

### US-273: futures_futures Stale Guard

**파일**: `engine/src/strategies/futures_futures.py`

**현재 상태**: Orderbook staleness 체크 없음.

**구현**:
1. `FuturesFuturesConfig`에 `max_book_age_seconds: float = Field(default=5.0, gt=0)` 추가
2. **[MUST FIX #5]** `on_signal()`에서 `book_age_ms = signal.metadata.get("book_age_ms")` — None이면 **FAIL CLOSED** (필터).
   Missing metadata = stale 가능성 → 안전하게 거부. `book_age_ms is None → filter + log WARNING "missing book_age_ms"`
3. `book_age_ms / 1000 > max_book_age_seconds` 이면 필터 + 로그
4. env var `FUTURES_MAX_BOOK_AGE_S` 오버라이드
5. **[MUST FIX #8]** env var `ENABLE_STALE_GUARD=true`

**AC**:
- [ ] book_age > 5s 시 시그널 거부
- [ ] env var 오버라이드 동작
- [ ] stale guard 테스트 PASS

---

### US-274: stat_arb Z-score 거래비용 조정

**파일**: `engine/src/strategies/statistical_arb.py`

**현재 상태**: Cross-asset 모드 `_evaluate_statistical_arb()` L560에서 `expected_profit_usdt=Decimal("0")` — 거래비용 미반영. Legacy `on_signal()` L915에서만 net_profit 게이트 있음.

**구현**:
1. `_evaluate_statistical_arb()` 진입 판단부 (L496~L570)에 cost gate 추가:
   ```python
   # cost gate: round-trip cost > expected spread profit -> skip
   buy_cost = self._cost_calculator.estimate_cost(exchange, symbol_a, BUY, size_a, mid_a)
   sell_cost = self._cost_calculator.estimate_cost(exchange, symbol_a, SELL, size_a, mid_a)
   buy_cost_b = self._cost_calculator.estimate_cost(exchange, symbol_b, BUY, size_b, mid_b)
   sell_cost_b = self._cost_calculator.estimate_cost(exchange, symbol_b, SELL, size_b, mid_b)
   round_trip_cost = buy_cost + sell_cost + buy_cost_b + sell_cost_b
   expected_spread_profit = abs(zscore) * _zscore_std(history) * notional_usd
   if round_trip_cost > Decimal(str(expected_spread_profit)):
       return None
   ```
2. `expected_profit_usdt` 를 `Decimal(str(expected_spread_profit - float(round_trip_cost)))` 로 변경

**AC**:
- [ ] 왕복 비용 > 예상수익 시 진입 거부
- [ ] expected_profit_usdt가 0이 아닌 실제 추정값
- [ ] 비용 게이트 테스트 PASS

---

### US-275: Atomic Fallback — Partial Fill 손절

**파일**: `engine/src/execution/atomic.py`

**현재 상태**: `execute()` (L66~L152)에서 partial fill 시 market fallback만 있고, timeout 후 잔여분 손절 없음.

**구현**:
1. `AtomicOrderExecutor.__init__`에:
   - `partial_fill_timeout_s: float = float(os.environ.get("PARTIAL_FILL_TIMEOUT_S", "30"))`
   - `max_loss_pct: float = float(os.environ.get("MAX_LOSS_PCT", "2.0"))`
2. **[MUST FIX #4]** Partial fill 소유권: `close_partial()`은 **AtomicOrderExecutor 내부**에서 처리.
   호출측(strategy)은 `execute()` 결과만 받음. 잔여분 추적 + 손절은 executor 책임.
   `execute()` 내부에서 `filled_size < size * 0.95` 감지 → 자체 손절 로직 실행 → 최종 결과에 포함.
3. 새 메서드 `async def _close_partial(self, exchange, symbol, side, remaining, entry_price) -> OrderResult` (private)
4. 손실이 `max_loss_pct` 초과 시 로그 CRITICAL
5. **[MUST FIX #8]** env var `ENABLE_PARTIAL_FILL_STOP=true`

**AC**:
- [ ] partial fill 30s 후 자동 손절
- [ ] MAX_LOSS_PCT 초과 시 CRITICAL 로그
- [ ] partial fill 손절 테스트 PASS

---

### US-275-a: DepthAnalyzer 주문 사이징 연결

**파일**: `engine/src/execution/atomic.py` + `engine/src/core/depth_analyzer.py`

**현재 상태**: DepthAnalyzer 0 consumers. vwap_for_buy/sell, liquidity_at_pct_depth 구현 완료이나 호출부 없음.

**구현**:
1. `AtomicOrderExecutor.__init__`에 `depth_analyzer: DepthAnalyzer | None = None` 파라미터
2. **[MUST FIX #9]** `execute()` 상단에서 book이 있으면 — multi-leg 시 **모든 leg를 비례 축소**:
   ```python
   if self._depth_analyzer and book:
       available = self._depth_analyzer.liquidity_at_pct_depth(book, Decimal("1"), side_str)
       if available < size:
           scale_factor = available * Decimal("0.8") / size
           size = size * scale_factor
           # 다른 leg도 동일 scale_factor 적용 (TradeRequest.legs 전체)
   ```
3. `execute()` 시그니처에 `book: OrderBook | None = None` 옵션 추가
4. **[MUST FIX #8]** env var `ENABLE_DEPTH_SIZING=true`

**AC**:
- [ ] DepthAnalyzer.liquidity_at_pct_depth() 호출 경로 존재
- [ ] depth < size 시 사이징 축소 동작
- [ ] book=None 시 기존 동작 유지 (backward compat)

**WIRING AC**:
- [ ] 생성: AtomicOrderExecutor에 depth_analyzer 필드
- [ ] 주입: engine 초기화에서 DepthAnalyzer 인스턴스 전달
- [ ] 호출: execute() 내에서 liquidity_at_pct_depth() 호출 확인

---

### US-276: S17 통합 Shadow 10min 검증

**실행 조건**: US-266~275-a 전량 완료 + pytest 전량 PASS

**검증 항목** (Shadow 13항목 복합지표):
1. PnL > 0
2. MDD% < 5%
3. Profit Factor > 1.2
4. Sharpe > 0 (10min 기준)
5. Calmar > 0
6. 전략별 trade >= 1 (triangular, funding_rate, spot_futures, futures_futures, stat_arb, cross_exchange)
7. 방어 레이어 활성 (LiveGate 6-check)
8. Crash 0건
9. OU 필터 로그 존재 (US-268, US-270)
10. Stale Guard 필터 로그 존재 (US-273)
11. Latency Budget 필터 로그 존재 (US-267)
12. Cost Gate 필터 로그 존재 (US-274)
13. DepthAnalyzer 호출 로그 존재 (US-275-a)

**AC**:
- [ ] Shadow 10min PnL > 0, Crash 0
- [ ] 12개 US 기능별 런타임 호출 증거 (로그/메트릭)
- [ ] `.omc/state/shadow-result-latest.json` 기록

---

## 6. Test Strategy

| 계층 | 대상 | 예상 테스트 수 |
|------|------|---------------|
| Unit | OUProcess (합성 데이터 3종) | 5 |
| Unit | Bellman-Ford 비용 통합 | 3 |
| Unit | Latency Budget 필터 | 2 |
| Unit | Funding 다중 거래소 | 3 |
| Unit | OU Basis (spot_futures) | 3 |
| Unit | max_holding_hours 강제 | 3 |
| Unit | Funding Convergence | 2 |
| Unit | Stale Guard | 2 |
| Unit | Cost Gate (stat_arb) | 3 |
| Unit | Partial Fill 손절 | 3 |
| Unit | DepthAnalyzer 사이징 | 3 |
| Integration | Shadow 10min | 1 |
| **합계** | | **~33** |

---

## 7. Risks

| 리스크 | 영향 | 완화 |
|--------|------|------|
| OU 프로세스 numpy 의존 | numpy 없는 환경에서 실패 | try/except + graceful fallback (static threshold) |
| Bellman-Ford 비용 통합 오차 | 수익성 과대/과소 추정 | fee_rate을 cost_calculator에서 정확히 가져오기 |
| Partial fill 손절 연쇄 반응 | 급등장에서 과도한 손절 | MAX_LOSS_PCT cap + 로그 CRITICAL |
| DepthAnalyzer 성능 | orderbook 깊이 연산 지연 | 1% depth만 확인 (O(n) where n=levels) |
| Funding rate API 지연 | 다중 거래소 polling 속도 | asyncio.gather + 개별 timeout |
| max_holding_hours monotonic 드리프트 | 장기 실행 시 시간 오차 | monotonic clock 사용 (wall clock 대비 안전) |

---

## 8. Execution Order (IVE TeamCreate)

```
Phase S17 실행 순서:

  [B1] 병렬 ─┬─ US-272 (futures_futures funding convergence)
              ├─ US-273 (futures_futures stale guard)
              └─ US-274 (stat_arb cost gate)

  [B2] 병렬 ─┬─ US-266 → US-267 (triangular: bellman-ford → latency budget)
              ├─ US-270 → US-271 (spot_futures: OU basis → max_holding_hours)
              └─ US-275 → US-275-a (atomic: partial fill → depth sizer)

  [B3] 순차 ── US-268 (OU Process) → US-269 (multi-exchange scanner)

  ──── pytest 전량 PASS 확인 ────

  [B4] ────── US-276 (Shadow 10min 통합 검증)
```

**참고**: B1, B2, B3은 서로 독립이므로 IVE TeamCreate에서 최대 6명 병렬 할당 가능:
- Yujin: US-272 + US-273 (같은 파일)
- Gaeul: US-274
- Leeseo: US-266 + US-267 (같은 전략)
- Liz: US-270 + US-271 (같은 전략)
- Wonyoung: US-275 + US-275-a (같은 파일)
- Rei: US-268 + US-269 (OU -> scanner 순차)

---

## 9. New Files

| 파일 | 용도 |
|------|------|
| `engine/src/core/ou_process.py` | OU 프로세스 헬퍼 (US-268, US-270 공용) |
| `engine/tests/test_ou_process.py` | OU 단위테스트 |
| `engine/tests/test_s17_*.py` | US별 테스트 파일 |

---

## 10. Modified Files Summary

| 파일 | US | 변경 내용 |
|------|-----|----------|
| `engine/src/core/triangular_scanner.py` | US-266 | _build_graph()에 fee+depth 비용 통합 |
| `engine/src/strategies/triangular.py` | US-267 | latency budget 500ms 필터 |
| `engine/src/core/ou_process.py` | US-268 | **신규** OU 프로세스 클래스 |
| `engine/src/strategies/funding_rate.py` | US-268 | OU 필터 추가 |
| `engine/src/core/multi_signal.py` | US-269 | bybit/okx funding rate 수집 |
| `engine/src/strategies/spot_futures.py` | US-270, US-271 | OU basis + max_holding_hours 강제 |
| `engine/src/strategies/futures_futures.py` | US-272, US-273 | funding convergence + stale guard |
| `engine/src/strategies/statistical_arb.py` | US-274 | cost gate (L496~L570) |
| `engine/src/execution/atomic.py` | US-275, US-275-a | partial fill 손절 + depth sizer |

---

## 11. Env Vars (신규)

| 변수 | 기본값 | US |
|------|--------|-----|
| `TRIANGULAR_MAX_LATENCY_MS` | `500` | US-267 |
| `FUNDING_OU_MIN_HALFLIFE_S` | `300` | US-268 |
| `FUNDING_SCANNER_EXCHANGES` | `binance_futures,bybit_futures,okx_futures` | US-269 |
| `BASIS_OU_MAX_HALFLIFE_H` | `24.0` | US-270 |
| `FUTURES_FUNDING_CONVERGENCE_WEIGHT` | `0.3` | US-272 |
| `FUTURES_MAX_BOOK_AGE_S` | `5.0` | US-273 |
| `PARTIAL_FILL_TIMEOUT_S` | `30` | US-275 |
| `MAX_LOSS_PCT` | `2.0` | US-275 |
