# Phase S2: Engine Wiring Completion — PLAN.md

> 생성: 2026-03-14 | Phase: S2 | US: 9개 (US-129~134, US-153~155)
> 소스: TF Semi-Final FAIL → Engine 모듈 미연결 보완

---

## 1. 목표

main.py에서 이미 구현된 모듈들(RiskGuardian, DynamicSizer, RegimeDetector, ONNX, SlippageFeedback, AtomicExecutor, TCA)의 **실행 경로를 연결**하여, 현재 dead code 상태인 기능을 실제 동작시킨다.

## 2. US 배치 그룹 (의존성 기반)

### Batch 1: 기반 모듈 연결 (독립, 병렬 가능)
| US | 제목 | 파일 | 복잡도 |
|----|------|------|--------|
| US-129 | RiskGuardian PortfolioState 실제 값 주입 | main.py, guardian.py | 중 |
| US-132 | SlippageFeedbackLoop LegResult 필드 수정 | paper.py, slippage.py | 중 |
| US-134 | TCA/Correlation ExecutionResult 필드 통일 | tca.py, correlation_monitor.py, paper.py | 중 |
| US-154 | RiskGuardian max_concurrent_positions 체크 추가 | guardian.py | 단순 |

### Batch 2: ML/Regime 연결 (US-129 완료 후)
| US | 제목 | 파일 | 복잡도 |
|----|------|------|--------|
| US-130 | DynamicSizer 실행 경로 연결 | sizer.py, signal.py, main.py | 중 |
| US-131 | RegimeDetector + ONNX Scorer main.py 주입 | main.py, signal.py | 중 |

### Batch 3: 실행 경로 (Batch 1 완료 후)
| US | 제목 | 파일 | 복잡도 |
|----|------|------|--------|
| US-133 | AtomicOrderExecutor(IOC) main.py 연결 | main.py, atomic.py | 단순 |
| US-153 | 주문 레벨 중복 방지 (Idempotency Key) | atomic.py, signal.py | 중 |
| US-155 | Graceful shutdown 시 오픈 포지션 정리 | main.py, atomic.py | 중 |

## 3. 상세 구현 계획

### US-129: RiskGuardian PortfolioState 실제 값 주입
**현재**: `_build_risk_check_fn()`에서 `used_capital=0`, `current_drawdown_pct=0` 고정
**목표**: 실제 값 주입
**구현**:
1. `_build_risk_check_fn()` 내에서 PortfolioState 8개 필드 전부 실제 값 주입:
   - `total_capital` = capital × len(exchanges) (기존 유지)
   - `used_capital` = VirtualBalanceTracker 또는 ShadowMode._metrics에서 active positions 총 노출
   - `current_drawdown_pct` = current drawdown / peak equity
   - `total_exposure` = 전체 포지션 노출 합계
   - `position_sizes` = symbol별 현재 포지션 (dict[str, Decimal])
   - `exchange_health_scores` = 거래소별 health check (dict[str, Decimal])
   - `volatility_1min` = PriceHub 최근 1분 변동성 (dict[str, Decimal])
   - `volatility_24h` = PriceHub 최근 24시간 변동성 (dict[str, Decimal])
2. `net_exposures` = ExposureTracker 또는 position 기반 계산
3. **핵심**: 현재 5개 필드 누락으로 TypeError → trade_consumer except 삼킴 → 9체크 전체 무력화 상태. 전 필드 주입 필수.
**테스트**: 과도한 포지션 시 REJECT 반환 검증, 필드 누락 시 TypeError 재현 방지

### US-130: DynamicSizer 실행 경로 연결
**현재**: DynamicSizer 초기화만 됨, 전략에서 호출 안 함
**목표**: SignalGenerator에서 compute_dynamic_size() 호출
**구현**:
1. SignalGenerator에 `dynamic_sizer` 파라미터 추가
2. `_evaluate_signal()`에서 sizer 호출하여 position size 결정
3. edge_bps/regime/bid_depth에 따라 크기 변동
**테스트**: sigmoid(5bps)→~0.38, sigmoid(50bps)→~0.98 검증

### US-131: RegimeDetector + ONNX Scorer main.py 주입
**현재**: HMMRegimeDetector, ONNXSignalScorer 미import/미생성
**목표**: main.py에서 생성 후 SignalGenerator에 전달
**구현**:
1. `_init_signal_pipeline()`에서:
   - `HMMRegimeDetector()` 생성 (optional, hmmlearn 없으면 None)
   - `ONNXSignalScorer(model_path=env.ONNX_MODEL_PATH)` 생성 (없으면 None)
2. SignalGenerator에 `regime_detector=detector`, `ml_scorer=scorer` 전달
3. graceful fallback: import 실패 시 None, 에러 아님
**테스트**: 레짐별 MIN_EDGE 동적 변경 로그, ONNX 없을 때 fallback

### US-132: SlippageFeedbackLoop LegResult 필드 수정
**현재**: LegResult에 expected_price, fill_price 필드 없음
**목표**: PaperExecutor가 fill 시 기록
**구현**:
1. LegResult에 `expected_price: Decimal | None`, `fill_price: Decimal | None` 추가
2. PaperExecutor._execute_leg()에서 값 설정
3. _on_execution_result()에서 SlippageFeedbackLoop.record_fill() 호출 시 활용
**테스트**: EMA 업데이트, get_adjusted_slippage() 변동 확인

### US-133: AtomicOrderExecutor(IOC) main.py 연결
**현재**: AtomicExecutor 초기화됨, Live 경로 미완
**목표**: Live 모드에서 IOC 주문 전송 경로 확인
**구현**:
1. main.py `_init_execution()`에서 EXECUTION_MODE=live 분기
2. Live: AtomicOrderExecutor로 실주문, Paper/Shadow: 기존 PaperExecutor 유지
3. TradeRequestConsumer에 executor 전달 시 모드 기반 선택
**테스트**: Live 모드 IOC 경로 확인 (mock), Paper/Shadow 기존 유지

### US-134: TCA/Correlation ExecutionResult 필드 통일
**현재**: TCA가 참조하는 필드와 ExecutionResult 필드 불일치 가능
**목표**: KeyError 0건
**구현**:
1. ExecutionResult/LegResult 필드 vs TCAAnalyzer.record_execution() 파라미터 대조
2. 누락 필드 추가 또는 기본값 설정
3. CorrelationMonitor.record_trade_pnl() 호출 시 필드 일치 확인
**테스트**: record_execution() 호출 시 KeyError 0건

### US-153: 주문 레벨 중복 방지 (Idempotency Key)
**현재**: 중복 주문 차단 메커니즘 없음
**목표**: 동일 signal_id 재처리 시 중복 주문 차단
**구현**:
1. AtomicOrderExecutor에 `_executed_keys: set[str]` 추가
2. key = f"{exchange}:{symbol}:{signal_id}:{timestamp_bucket}"
3. 동일 key 존재 시 스킵 + 로그
4. TTL 기반 정리 (5분 윈도우)
**테스트**: 동일 신호 2회 전송 → 1건만 실행

### US-154: RiskGuardian max_concurrent_positions 체크 추가
**현재**: 동시 포지션 수 체크 없음
**목표**: 최대 20개 동시 포지션 제한
**구현**:
1. RiskGuardian에 `max_concurrent_positions` 파라미터 (기본 20)
2. CHECK #10: `len(portfolio.position_sizes) >= max_concurrent_positions` → REJECT
3. MAX_CONCURRENT_POSITIONS 환경변수로 조정 가능
**테스트**: 175 심볼 동시 진입 시 20개 초과분 REJECT

### US-155: Graceful shutdown 시 오픈 포지션 정리
**현재**: Engine.stop()에서 단순 종료
**목표**: SIGTERM 시 미체결 주문 취소
**구현**:
1. Engine.stop()에 shutdown cleanup 추가
2. Live 모드: pending orders 전량 취소 (cancel_all_orders)
3. 취소 실패 시 Telegram 알림 + 로그
4. shutdown 후 pending_orders == 0 확인
**테스트**: shutdown 시 cancel 호출, 실패 시 알림

## 4. 파일 변경 범위

| 파일 | US | 변경 내용 |
|------|-----|----------|
| engine/src/main.py | 129,130,131,133,155 | _init_signal_pipeline, _init_risk, _init_execution, stop() |
| engine/src/risk/guardian.py | 129,154 | PortfolioState 생성, max_concurrent_positions |
| engine/src/core/signal.py | 130,131 | dynamic_sizer, regime_detector, ml_scorer 연결 |
| engine/src/execution/sizer.py | 130 | compute_dynamic_size 호출 경로 |
| engine/src/execution/paper.py | 132,134 | LegResult 필드 추가 |
| engine/src/execution/atomic.py | 133,153,155 | IOC 연결, idempotency, shutdown |
| engine/src/risk/slippage.py | 132 | record_fill 호출 확인 |
| engine/src/analysis/tca.py | 134 | 필드 통일 |
| engine/src/risk/correlation_monitor.py | 134 | 필드 통일 |

## 5. 위험 요소

1. **이중 슬리피지 금지**: SignalGenerator의 CEXOrderbookSlippage가 유일한 소스. DynamicSizer/RegimeDetector 연결 시 추가 슬리피지 적용 금지
2. **hmmlearn/onnxruntime 미설치**: graceful fallback 필수 (None 반환, 에러 아님)
3. **PortfolioState 실제 값**: Shadow 모드에서 VirtualBalanceTracker 기반, Live에서 실 잔고 기반
4. **기존 테스트 깨짐 방지**: LegResult 필드 추가 시 기존 테스트의 fixture 업데이트 필요

## 6. 팀 배정

- **Yujin** (executor): Batch 1 (US-129, US-132, US-134, US-154) — engine/src/ 백엔드
- **Gaeul** (executor): Batch 2+3 (US-130, US-131, US-133, US-153, US-155) — engine/src/ 병렬
- **Wonyoung** (test-engineer): 전 US 테스트 작성 + pytest 실행
