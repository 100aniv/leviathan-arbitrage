# Phase S8: System Integration Hardening — PLAN.md

> **작성일**: 2026-03-15
> **작성자**: Planner (AESPA Giselle)
> **Entry Gate**: PASS (Karina, 2026-03-15)
> **US 범위**: US-169 ~ US-180 (12개)
> **목적**: 구현 완료이나 main.py 초기화 체인에서 미연결된 12개 기능을 엔진에 실제 연결
> **SSOT 참조**: `SSOT.md` §9 GAP 분석 + §2 Phase S8 상태

---

## 1. 배경 및 문제 정의

### 1.1 핵심 패턴
Phase A~M + S1~S7에서 개별 모듈은 모두 구현 완료되었으나, **main.py 초기화 체인에서 실제로 연결되지 않은 기능 12개**가 TF QF/SF 과정에서 발견됨. 모듈 코드는 존재하지만 엔진 런타임에서 호출되지 않아 사실상 비활성 상태.

### 1.2 심각도 분류

| 심각도 | 건수 | US |
|--------|------|----|
| **CRITICAL** | 3 | US-169, US-170, US-171 |
| **HIGH** | 5 | US-172, US-173, US-174, US-175, US-176 |
| **MEDIUM** | 4 | US-177, US-178, US-179, US-180 |

### 1.3 공통 수정 대상
- `engine/src/main.py` — 12개 US 중 10개가 이 파일의 초기화 체인 수정 필요
- `engine/src/core/signal.py` — US-172, US-173 (ML/Regime 신호 파이프라인)
- `engine/src/modes/shadow.py` — US-171 (KRW staleness → KillSwitch)

---

## 2. US 상세 명세

### US-169: MultiStrategySignalProducer LIVE 모드 연결 [CRITICAL]

**현재 상태**: `MultiStrategySignalProducer`가 Paper/Shadow 모드에서만 생성됨 (`_shadow_mode_loop`, `_progressive_shadow_loop`, `_strategy_validation_loop`). LIVE 모드 진입 시 `_real_data_feed_loop`에서 `MultiStrategySignalProducer`를 생성하지 않아 **5/8 전략 (spot_futures, futures_futures, triangular, funding_rate, latency_arb)의 신호가 0건**.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | `_real_data_feed_loop()` 내 `MultiStrategySignalProducer` 생성 + `RealSignalProducer` 연결 | ~50줄 |

**변경 내용**:
1. `_real_data_feed_loop()` 시작부에 `MultiStrategySignalProducer` 인스턴스 생성
2. `RealSignalProducer` 생성하여 `on_orderbook` 콜백에서 추가 전략 신호 생산
3. `FundingRateCollector` 시작하여 funding rate 데이터 수집
4. 기존 Shadow/Paper와 동일한 패턴 적용 (copy from `_shadow_mode_loop` lines 1574-1597)

**테스트 전략**:
- 단위: `MultiStrategySignalProducer` 생성 확인 mock 테스트
- 통합: DATA_MODE=real_public 시 multi_signal_producer is not None 검증
- 기존 테스트 회귀 확인

**수용 기준**:
- [ ] LIVE/real_public 모드에서 `MultiStrategySignalProducer` 인스턴스 생성됨
- [ ] 8개 전략 모두 신호 생성 경로 활성화
- [ ] 기존 Shadow/Paper 모드 동작 불변

---

### US-170: TriangularScanner LIVE 연결 [CRITICAL]

**현재 상태**: `TriangularScanner` 클래스(`engine/src/core/triangular_scanner.py`)는 구현 완료. Shadow 모드에서는 `shadow.py:501`에서 `TriangularScanner()`를 생성하지만, **main.py의 LIVE/Paper 경로에서는 생성되지 않음**. `TriangularStrategy`는 등록되지만 scanner 없이는 삼각 차익 기회를 탐지 불가.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | `_real_data_feed_loop()` 내 `TriangularScanner` + `RealSignalProducer` 연결 | ~30줄 |

**변경 내용**:
1. `_real_data_feed_loop()` 에서 `TriangularScanner()` 인스턴스 생성
2. `RealSignalProducer`에 `triangular_scanner` 파라미터로 전달
3. `on_orderbook` 콜백에서 `RealSignalProducer.evaluate_triangular()` 호출

**테스트 전략**:
- 단위: TriangularScanner 인스턴스화 + 신호 생성 mock
- 통합: 3-symbol 오더북 데이터에서 삼각 기회 탐지 확인

**수용 기준**:
- [ ] LIVE 모드에서 `TriangularScanner` 활성
- [ ] 삼각 차익 신호가 `RealSignalProducer`를 통해 전파됨
- [ ] Shadow 모드 기존 scanner 동작 불변

---

### US-171: KRW Staleness → KillSwitch 연결 [CRITICAL]

**현재 상태**: `shadow.py`에서 KRW 환율 120초 stale 감지 시 `self._krw_stale = True`로 설정하고 **KRW 심볼 신호만 필터링** (line 740). 그러나 **KillSwitch에 통보하지 않아** 이미 진행 중인 KRW 거래가 계속 실행될 수 있음.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/modes/shadow.py` | KRW stale 진입 시 `halt_local()` 또는 KRW 전용 kill switch 호출 | ~15줄 |
| `engine/src/risk/kill_switch.py` | KRW 전용 심볼 차단 메서드 추가 (선택) | ~10줄 |

**변경 내용**:
1. `shadow.py` KRW stale 진입 시 (`_krw_stale = True`):
   - 옵션 A: `halt_local()` 호출 (전체 엔진 중단 — 보수적)
   - 옵션 B: KRW 심볼 전용 블랙리스트 (signal_generator.block_symbols) + Telegram 알림
   - **권장: 옵션 B** — 전체 엔진 중단은 과도, KRW 심볼만 차단이 적절
2. stale 복구 시 블랙리스트 해제
3. Telegram CRITICAL 알림 전송

**테스트 전략**:
- 단위: KRW stale 진입 시 심볼 차단 확인
- 단위: stale 복구 시 차단 해제 확인
- 통합: Shadow 10min에서 KRW stale 시뮬레이션

**수용 기준**:
- [ ] KRW 120s stale 시 KRW 심볼 거래 즉시 중단
- [ ] Stale 복구 시 자동 재개
- [ ] Telegram CRITICAL 알림 발송
- [ ] 비-KRW 거래는 영향 없음

---

### US-172: ONNX ML Scorer signal.py 연결 [HIGH]

**현재 상태**: `main.py:672-678`에서 `ONNXSignalScorer`를 로드하여 `SignalGenerator`에 `ml_scorer`로 전달하지만, **signal.py에서 `self._ml_scorer`를 실제로 호출하는 코드가 없음**. 인스턴스는 존재하나 `score()` 호출이 누락.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/core/signal.py` | `_evaluate_signal()` 또는 `on_orderbook_update()` 내에서 `ml_scorer.score()` 호출 + 임계값 게이트 | ~30줄 |

**변경 내용**:
1. Signal 생성 파이프라인에서 friction filter 후, ml_scorer가 있으면 `score()` 호출
2. ML 점수가 임계값(env var `ML_SCORE_THRESHOLD`, default 0.5) 미만이면 신호 거부
3. 신호 메타데이터에 `ml_score` 필드 추가 (모니터링/분석용)
4. ml_scorer 없으면 기존 동작 유지 (graceful fallback)

**테스트 전략**:
- 단위: ml_scorer mock → score() 호출 + 임계값 필터링
- 단위: ml_scorer=None → 기존 동작 불변
- 기존 signal 테스트 회귀 확인

**수용 기준**:
- [ ] ml_scorer가 있으면 모든 신호에 score() 호출됨
- [ ] ML 점수 < 임계값 → 신호 거부
- [ ] ml_scorer=None → 기존 동작 100% 유지

---

### US-173: HMM RegimeDetector predict() 호출 [HIGH]

**현재 상태**: `main.py:654-667`에서 `HMMRegimeDetector` 초기화. `signal.py:246-263`에서 `self._regime_detector.current_regime`을 읽어 min_edge를 조정하지만, **`predict()`가 호출되지 않아 `current_regime`이 항상 초기값(NORMAL)에 고정**. 피쳐 데이터를 주기적으로 predict()에 공급하는 루프가 없음.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | 백그라운드 태스크에 regime 업데이트 루프 추가 | ~30줄 |

**변경 내용**:
1. `_start_background_tasks()`에 `_regime_update_loop()` 추가
2. 60초 주기로 최근 가격 데이터에서 피쳐 추출 → `regime_detector.predict(features)` 호출
3. PriceHub에서 BTC/USDT 가격 히스토리 추출 (returns, volatility 등)
4. predict() 결과로 `current_regime` 자동 업데이트 → signal.py의 기존 min_edge 조정 로직 활성화

**테스트 전략**:
- 단위: mock PriceHub + mock HMMRegimeDetector → predict() 호출 확인
- 단위: regime 변경 시 min_edge 조정 확인
- 통합: Shadow 10min에서 regime 변경 로그 확인

**수용 기준**:
- [ ] predict()가 60초 주기로 호출됨
- [ ] regime 변경 시 signal.py의 min_edge 동적 조정 활성화
- [ ] RegimeDetector 미사용 시 기존 동작 유지

---

### US-174: AdaptiveThreshold 엔진 연결 [HIGH]

**현재 상태**: `AdaptiveThreshold` 클래스 (`engine/src/tuning/adaptive_threshold.py`)는 94줄 완전 구현 (WR 기반 MIN_EDGE_BPS 자동 조정). 그러나 **main.py에서 인스턴스화하지 않아** MIN_EDGE가 영원히 정적.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | AdaptiveThreshold 인스턴스 생성 + 1시간 주기 조정 루프 | ~35줄 |

**변경 내용**:
1. `_init_signal_pipeline()` 에서 `AdaptiveThreshold(initial_edge_bps=min_edge_bps)` 인스턴스 생성
2. `Engine` 인스턴스 변수 `self._adaptive_threshold` 추가
3. `_start_background_tasks()`에 `_adaptive_threshold_loop()` 추가
4. 1시간 주기로 Shadow/Paper의 WR + trade count 집계 → `adjust()` 호출
5. 조정된 edge를 `signal_generator._config.min_edge`에 반영 + 로그

**테스트 전략**:
- 단위: AdaptiveThreshold.adjust() WR 시나리오별 테스트 (이미 존재)
- 단위: main.py 인스턴스화 mock 테스트
- 통합: 1시간 후 edge 변경 로그 확인 (시뮬레이션)

**수용 기준**:
- [ ] AdaptiveThreshold 인스턴스 생성됨
- [ ] 1시간 주기로 adjust() 호출
- [ ] 조정된 edge가 SignalGenerator에 반영됨

---

### US-175: ExposureTracker 인스턴스화 + RiskGuardian 연결 [HIGH]

**현재 상태**: `ExposureTracker` 클래스 (`engine/src/risk/exposure_tracker.py`)는 Redis 기반 순노출도 추적 구현 완료. `RiskGuardian.check()`에서 `exposure_tracker`를 참조하는 코드도 존재 (`guardian.py:74`). 그러나 **main.py에서 `ExposureTracker`를 생성하지 않아** 순노출도 추적 불가.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | `_init_risk()`에서 ExposureTracker 생성 + RiskGuardian 연결 | ~20줄 |
| `engine/src/main.py` | `_on_execution_result()`에서 ExposureTracker.update_exposure() 호출 | ~15줄 |

**변경 내용**:
1. `_init_risk()`에서 Redis 사용 시 `ExposureTracker(redis_client=self._redis_client)` 생성
2. `self._risk_guardian.exposure_tracker = self._exposure_tracker` 연결
3. `_on_execution_result()`에서 거래 성공 시 `update_exposure()` 호출 (비동기)
4. Redis 미사용 시 (paper 모드) ExposureTracker 스킵 + 경고 로그

**테스트 전략**:
- 단위: ExposureTracker 생성 + update/get 호출 mock
- 단위: RiskGuardian check() 시 exposure_tracker 참조 확인
- 통합: Redis 모드에서 노출도 Redis 키 확인

**수용 기준**:
- [ ] LIVE 모드에서 ExposureTracker 인스턴스 생성됨
- [ ] 거래 실행 후 순노출도 Redis에 기록
- [ ] Paper 모드에서 graceful skip

---

### US-176: CorrelationMonitor → DynamicSizer 포지션 축소 연결 [HIGH]

**현재 상태**: `CorrelationMonitor`가 `_init_risk()`에서 생성되고 `RiskGuardian`에 연결됨 (`main.py:888-895`). Check #9에서 높은 상관관계 감지 시 **로그만 출력**하고 `DynamicSizer`에 포지션 축소 신호를 보내지 않음.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | CorrelationMonitor → DynamicSizer 연결 + correlation penalty 적용 | ~25줄 |
| `engine/src/execution/sizer.py` | DynamicSizer에 `correlation_penalty` 파라미터 추가 (선택) | ~15줄 |

**변경 내용**:
1. `_init_execution()` 또는 별도 wiring 단계에서 `DynamicSizer.correlation_monitor = self._correlation_monitor` 연결
2. `DynamicSizer.compute_size()`에서 `correlation_monitor.get_correlation_penalty()` 조회
3. 상관관계 높으면 포지션 사이즈 * (1 - penalty) 축소
4. 대안: `_on_execution_result()`에서 correlation 체크 후 경고 → Telegram 알림

**테스트 전략**:
- 단위: CorrelationMonitor mock → 높은 상관관계 시 DynamicSizer 축소 확인
- 단위: 낮은 상관관계 시 사이즈 불변 확인
- 통합: Shadow에서 상관관계 로그 + 축소 로그 확인

**수용 기준**:
- [ ] 높은 상관관계(>0.7) 감지 시 포지션 사이즈 자동 축소
- [ ] DynamicSizer에 correlation_monitor 참조 연결됨
- [ ] 상관관계 정상일 때 사이즈 영향 없음

---

### US-177: DEX 실연결 — _build_dex_adapter() 시그니처 수정 [MEDIUM]

**현재 상태**: `_build_dex_adapter()` (main.py:833-850)가 `UniswapV3Adapter`를 생성하지만, `DEXCostCalculator`에 `GasOracle`을 전달하지 않음. 또한 `UniswapV3Adapter` 생성 시 `chain_id`, `wallet_address` 등 필수 파라미터 누락.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/main.py` | `_build_dex_adapter()` 시그니처 확장 + GasOracle 연결 | ~15줄 |

**변경 내용**:
1. `_build_dex_adapter()`에 `DEX_CHAIN_ID`, `DEX_WALLET_ADDRESS` env var 읽기 추가
2. `UniswapV3Adapter` 생성 시 `chain_id`, `wallet_address` 전달
3. `DEXCostCalculator` 생성 시 `GasOracle()` 인스턴스 전달
4. 필수 env var 미설정 시 명확한 경고 로그

**테스트 전략**:
- 단위: env var 설정 시 adapter 생성 확인
- 단위: env var 미설정 시 None 반환 + 경고 로그

**수용 기준**:
- [ ] DEX env vars 설정 시 UniswapV3Adapter + GasOracle 완전 연결
- [ ] DEXCostCalculator에 gas_oracle 인스턴스 전달
- [ ] env vars 미설정 시 기존 동작 (None 반환) 유지

---

### US-178: IOC Limit Order 주요 거래소 구현 [MEDIUM]

**현재 상태**: `AtomicOrderExecutor` (`engine/src/execution/atomic.py`)가 `exchange.place_ioc_limit()` 호출하지만, **Native Adapter들 (Binance, Bybit, OKX)에 `place_ioc_limit()` 메서드가 구현되지 않음**. NativeAdapter 기본 인터페이스에 `place_order(order)` 만 존재.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/infra/exchange/native_adapter.py` | `place_ioc_limit()` 추상 메서드 추가 | ~10줄 |
| `engine/src/infra/exchange/native_binance.py` | Binance IOC limit 구현 (`timeInForce: IOC`) | ~40줄 |
| `engine/src/infra/exchange/native_bybit.py` | Bybit IOC limit 구현 (`timeInForce: IOC`) | ~40줄 |
| `engine/src/infra/exchange/native_okx.py` | OKX IOC limit 구현 (`ordType: ioc`) | ~40줄 |
| `engine/src/infra/exchange/native_bitget.py` | Bitget IOC fallback (미지원 시 market order) | ~20줄 |

**변경 내용**:
1. `NativeAdapter` ABC에 `place_ioc_limit(symbol, side, price, size)` 추상 메서드 추가 (default: NotImplementedError → market fallback)
2. Binance: POST `/api/v3/order` with `type=LIMIT&timeInForce=IOC`
3. Bybit: POST `/v5/order/create` with `orderType=Limit&timeInForce=IOC`
4. OKX: POST `/api/v5/trade/order` with `ordType=ioc`
5. Bitget/Upbit/Bithumb/Coinone: default fallback (market order)
6. 모든 구현에 `filled_size`, `avg_price` 응답 파싱 포함

**테스트 전략**:
- 단위: 각 거래소 IOC 응답 파싱 mock 테스트
- 단위: partial fill → market fallback 시나리오
- 단위: timeout → market fallback 시나리오
- 통합: AtomicOrderExecutor → NativeAdapter IOC 호출 E2E

**수용 기준**:
- [ ] Binance, Bybit, OKX에서 IOC limit order 실행 가능
- [ ] Partial fill 시 나머지 market order로 fallback
- [ ] 미지원 거래소는 안전한 market order fallback
- [ ] IOC fill rate Prometheus 메트릭 기록

---

### US-179: ScheduledTuner 핫리로드 + 기본 활성화 [MEDIUM]

**현재 상태**: `ScheduledTuner` (`engine/src/tuning/scheduled_tuner.py`)가 `_init_tuner()`에서 생성되지만, **최적화 결과가 `config/strategy_params.json`에 저장된 후 엔진 재시작 없이 반영되지 않음**. 또한 `ENABLE_INLINE_TUNER` env var가 기본 비활성.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/tuning/scheduled_tuner.py` | `_apply_results()` 후 SignalGenerator/Strategy 핫리로드 콜백 | ~20줄 |
| `engine/src/main.py` | ScheduledTuner에 핫리로드 콜백 전달 | ~10줄 |

**변경 내용**:
1. `ScheduledTuner.__init__()`에 `on_params_updated: Callable` 콜백 파라미터 추가
2. 최적화 완료 후 콜백 호출 → 엔진이 `strategy_params.json` 재로드
3. `_init_tuner()`에서 콜백 전달: `lambda: self._reload_strategy_params()`
4. `Engine._reload_strategy_params()` 메서드 추가: JSON 파일 재읽기 → 전략 설정 업데이트

**테스트 전략**:
- 단위: 콜백 호출 시 파라미터 재로드 확인
- 단위: 콜백 없으면 기존 동작 유지
- 통합: ScheduledTuner 실행 후 파라미터 변경 반영 확인

**수용 기준**:
- [ ] ScheduledTuner 최적화 완료 시 핫리로드 콜백 호출
- [ ] 엔진 재시작 없이 전략 파라미터 반영됨
- [ ] 콜백 실패 시 기존 파라미터 유지 (안전 fallback)

---

### US-180: InMemoryEventBus 큐 크기 제한 [MEDIUM]

**현재 상태**: `InMemoryEventBus` (`engine/src/infra/redis/memory_bus.py`)가 `asyncio.Queue()`를 크기 제한 없이 생성. 장시간 실행 시 메모리 무한 증가 가능.

**수정 파일**:
| 파일 | 변경 내용 | 예상 줄 수 |
|------|----------|-----------|
| `engine/src/infra/redis/memory_bus.py` | `asyncio.Queue(maxsize=N)` + env var + drop-oldest 전략 | ~15줄 |

**변경 내용**:
1. `InMemoryEventBus.__init__()`에 `maxsize` 파라미터 추가 (default: `EVENT_BUS_MAX_SIZE` env var, fallback 10000)
2. 큐 가득 찼을 때 oldest 메시지 drop + warning 로그
3. Prometheus 카운터 `event_bus_drops_total` 추가
4. 기존 동작과 호환 유지 (maxsize=0이면 무제한)

**테스트 전략**:
- 단위: maxsize 초과 시 drop + 카운터 증가 확인
- 단위: maxsize=0 시 기존 무제한 동작 확인
- 기존 EventBus 테스트 회귀 확인

**수용 기준**:
- [ ] 큐 크기 제한 env var로 설정 가능
- [ ] 초과 시 oldest drop + 로그 + 메트릭
- [ ] 기존 테스트 통과

---

## 3. 배치 실행 순서

### 의존성 그래프

```
Batch 1 (독립, 즉시 병렬)          Batch 2 (main.py _init 체인)
┌─────────────────────────┐       ┌─────────────────────────────────┐
│ US-177: DEX 실연결       │       │ US-169: MultiSignal LIVE 연결    │
│ US-180: EventBus maxsize │       │ US-170: TriangularScanner 연결   │
│ US-179: Tuner 핫리로드    │       │ US-171: KRW → KillSwitch        │
└─────────────────────────┘       └──────────────┬──────────────────┘
                                                  │ (main.py 초기화 순서 의존)
                                                  ▼
                                  Batch 3 (signal.py + 리스크 체인)
                                  ┌─────────────────────────────────┐
                                  │ US-172: ONNX ML Scorer 연결      │
                                  │ US-173: HMM predict() 루프       │
                                  │ US-174: AdaptiveThreshold 연결   │
                                  │ US-175: ExposureTracker 연결     │
                                  │ US-176: Correlation→Sizer 연결   │
                                  └──────────────┬──────────────────┘
                                                  │ (리스크 모듈 의존)
                                                  ▼
                                  Batch 4 (외부 API 의존)
                                  ┌─────────────────────────────────┐
                                  │ US-178: IOC Limit (3 exchanges)  │
                                  └─────────────────────────────────┘
```

### 실행 순서

| 배치 | US | 우선순위 | 예상 줄 수 | 병렬 가능 | 의존성 |
|------|-----|---------|-----------|----------|--------|
| **1** | US-177, US-180, US-179 | MEDIUM | ~50줄 | 3건 모두 독립 | 없음 |
| **2** | US-169, US-170, US-171 | CRITICAL | ~95줄 | 169+170 병렬, 171 독립 | Batch 1 완료 불필요 |
| **3** | US-172, US-173, US-174, US-175, US-176 | HIGH | ~170줄 | 172+173 병렬, 174 독립, 175+176 순차 | Batch 2의 main.py 패턴 확립 후 |
| **4** | US-178 | MEDIUM | ~150줄 | 단일 | Batch 1~3 완료 후 (통합 테스트 필요) |

**총 예상 변경량**: ~465줄 (신규 코드 + 테스트)

---

## 4. 위험 요소 및 완화 방안

### 4.1 CRITICAL 위험

| # | 위험 | 영향 | 완화 방안 |
|---|------|------|----------|
| R1 | main.py 초기화 순서 충돌 | 10개 US가 main.py 수정 → merge conflict | **순차 커밋**: Batch별 단일 커밋, 각 커밋 후 pytest 통과 확인 |
| R2 | Shadow 모드 regression | KRW stale 연결(US-171) 시 기존 Shadow 동작 파괴 | **Shadow 10min 회귀 테스트**: 각 Batch 후 반드시 실행 |
| R3 | ONNX/HMM 런타임 의존성 | onnxruntime/hmmlearn 미설치 환경에서 crash | **graceful fallback**: try/except + None 체크 유지 |

### 4.2 HIGH 위험

| # | 위험 | 영향 | 완화 방안 |
|---|------|------|----------|
| R4 | IOC 구현 거래소 API 변경 | Binance/Bybit/OKX API 스펙 차이 | **API 문서 참조** + 각 거래소 별도 테스트 |
| R5 | ExposureTracker Redis 의존 | Paper 모드에서 Redis 없으면 crash | **조건부 생성**: Redis 활성 시에만 인스턴스화 |
| R6 | AdaptiveThreshold 과도한 조정 | WR 일시 하락 시 edge 급등 → 거래 0건 | **min/max 범위 제한**: min_edge=2, max_edge=50 (이미 구현) |

### 4.3 MEDIUM 위험

| # | 위험 | 영향 | 완화 방안 |
|---|------|------|----------|
| R7 | EventBus drop oldest | 정상 신호 유실 가능 | **maxsize 충분히 크게** (10000) + drop 카운터 모니터링 |
| R8 | ScheduledTuner 핫리로드 race condition | 실행 중 파라미터 변경 시 일관성 | **asyncio.Lock** 또는 copy-on-write 패턴 |

---

## 5. 테스트 전략 요약

### 5.1 단위 테스트 (각 US별)

| 범주 | 예상 테스트 수 | 대상 |
|------|-------------|------|
| main.py 초기화 | 12 | 모듈 생성/연결 확인 |
| signal.py ML/Regime | 8 | ONNX score, regime predict |
| shadow.py KRW stale | 6 | 차단/해제/알림 |
| IOC native adapters | 12 | 3 exchanges x 4 scenarios |
| EventBus maxsize | 4 | drop, counter, fallback |
| AdaptiveThreshold | 기존 | 이미 구현됨, 회귀 확인 |
| **합계** | **~42** | |

### 5.2 통합 테스트

- Shadow 10min: 각 Batch 완료 후 실행 (crash=0, PnL>0, WR>60%)
- pytest 전체: 각 커밋 후 `cd engine && python -m pytest tests/ -x --tb=short` (4,474+ 통과)

### 5.3 완료 기준

```
1. pytest: 4,474 + ~42 신규 = 4,516+ passed, 0 failed
2. Shadow 10min: crash=0, PnL>0, WR>60%
3. 12개 US 모두 SSOT.md에 체크 (✅)
4. prd.json: 12개 US status=pass 업데이트
```

---

## 6. 구현 가이드라인

### 6.1 코딩 규칙
- 모든 새로운 연결은 `try/except` + graceful fallback 필수
- env var 기본값은 기존 동작 유지 (backward compatible)
- 로그 형식: `module_name.event_name` (structlog 패턴)
- 테스트: `tests/unit/` 하위에 기존 모듈별 디렉토리 따름

### 6.2 커밋 전략
```
Batch 1: "Phase S8 Batch 1: EventBus maxsize + DEX adapter + Tuner hot-reload (US-177,179,180)"
Batch 2: "Phase S8 Batch 2: MultiSignal LIVE + TriangularScanner + KRW KillSwitch (US-169,170,171)"
Batch 3: "Phase S8 Batch 3: ONNX ML + HMM Regime + AdaptiveThreshold + Exposure + Correlation (US-172~176)"
Batch 4: "Phase S8 Batch 4: IOC Limit Order for Binance/Bybit/OKX (US-178)"
```

### 6.3 금지 사항
- PaperExecutor에 슬리피지 이중 적용 금지 (SignalGenerator의 CEXOrderbookSlippage가 유일)
- main.py 초기화 순서 변경 금지 (기존: config→infra→exchange→signal→strategy→risk→execution)
- ENGINE_ENV에 `development` 사용 금지 (`dev|staging|prod|test`만)

---

## 7. Phase S8 완료 후 다음 단계

```
Phase S8 완료 → pytest PASS → Shadow 10min PASS
    → TF QF 재실행 (TWICE 9명, 142항목 + S8 12항목)
    → TF QF PASS → TF SF [단계 1-A]부터 재시작
    → TF SF PASS → TF Final
    → TF Final PASS → Live
```

---

> **END OF PLAN** — Phase S8: System Integration Hardening
> 12 User Stories, 4 Batches, ~465 lines of changes, ~42 new tests
