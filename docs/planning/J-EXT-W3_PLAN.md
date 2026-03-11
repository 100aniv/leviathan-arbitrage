# Phase J-EXT Wave 3 — 엔진 기능 강화 PLAN

## 배치 구조

### Batch 1 (engine-only, 5 US)
독립적, 파일 겹침 없음, 동시 구현 가능.

| US | 제목 | 신규 파일 | 수정 파일 |
|----|------|-----------|-----------|
| US-114 | 동적 포지션 사이징 | - | `engine/src/execution/sizer.py` (확장) |
| US-115 | 슬리피지 피드백 루프 | `engine/src/risk/slippage.py` | `engine/src/execution/paper.py` |
| US-117 | 텔레그램 양방향 명령어 | `engine/src/infra/telegram_bot.py` | - |
| US-118 | 전략 간 상관관계 모니터링 | `engine/src/risk/correlation_monitor.py` | `engine/src/risk/guardian.py` |
| US-119 | IOC 주문 타입 | - | `engine/src/execution/atomic.py` (신규) |

### Batch 2 (cross-domain, 1 US)
| US | 제목 | 신규 파일 |
|----|------|-----------|
| US-116 | TCA 모듈 + 레이턴시 위젯 | `engine/src/analysis/tca.py`, `dashboard/src/components/TCAWidget.tsx` |

### Batch 3 (engine critical, 1 US)
| US | 제목 | 신규/수정 파일 |
|----|------|---------------|
| US-120 | 인벤토리 리밸런싱 | `engine/src/execution/inventory_rebalancer.py` (신규), `engine/src/main.py` (수정) |

---

## US-114: 동적 포지션 사이징

**현재**: `PositionSizer`는 Kelly criterion + 자본 등급 제약만 적용.
**변경**: `DynamicSizer` 래퍼 추가 — `size = base_size × confidence(edge) × regime_multiplier × liquidity_factor`

구현:
- `DynamicSizer` 클래스: `PositionSizer` 위에 3가지 승수 적용
- `confidence(edge)`: edge_bps에 비례 (5bps → 0.5, 50bps → 1.0, sigmoid 스케일링)
- `regime_multiplier`: CRISIS=0.25, HIGH=0.75, NORMAL=1.0, LOW_VOL=1.5 (4종 enum, RegimeDetector 연동)
- `liquidity_factor`: orderbook depth 기반 (bid_depth / threshold, max 1.0)
- 기존 `PositionSizer.compute_size()` 호출 후 승수 적용

테스트:
- edge 5bps vs 50bps 사이즈 비율 차이 확인
- CRISIS 레짐 → 25% 사이즈 확인
- liquidity_factor 경계값 테스트

## US-115: 슬리피지 피드백 루프

**현재**: `SlippageModel`은 정적 `base_slippage_pct` 사용.
**변경**: `SlippageFeedbackLoop` — 실제 체결가 vs 예상 비교 후 EMA 자동 조정.

구현:
- `engine/src/risk/slippage.py`: `SlippageFeedbackLoop` 클래스
  - `record_fill(expected_price, actual_price, side)` → 슬리피지 오차 기록
  - EMA(alpha=0.1)로 `adjustment_factor` 업데이트
  - `get_adjusted_slippage(base_slippage)` → 조정된 값 반환
- **이중계산 방지 규칙**:
  - `get_adjusted_slippage()`는 **BookWalkSlippage._fallback_bps 파라미터 보정 전용**
  - fill_price 이후 추가 슬리피지 적용 **절대 금지**
  - 보정 대상: `BookWalkSlippage._fallback_bps` (실행 시점 SlippageModel.apply() 호출 전 입력값 조정)
  - CEXOrderbookSlippage(SignalGenerator용)와 완전 독립 — 교차 적용 없음
- Prometheus: `slippage_adjustment_gauge`, `slippage_error_histogram`
- `paper.py` 연동: SlippageFeedbackLoop 인스턴스를 참조만 제공 (PaperExecutor 내부에서 직접 호출하지 않음)

테스트:
- EMA 수렴 확인 (10회 기록 후 adjustment 수렴)
- 과소추정 → 양수 조정, 과대추정 → 음수 조정

## US-117: 텔레그램 양방향 명령어

**현재**: `TelegramAlerter`는 단방향 (알림 전송만).
**변경**: `TelegramCommandHandler` — polling 기반 양방향 명령어 처리.

구현:
- `engine/src/infra/telegram_bot.py`: `TelegramCommandHandler`
  - getUpdates long-polling (30s timeout)
  - 5개 명령어: `/status`, `/kill`, `/mode`, `/balance`, `/help`
  - 기존 `TelegramAlerter` 인스턴스로 응답 전송
  - 알 수 없는 명령어 → 도움말 메시지
- 비동기 루프: `asyncio.create_task(handler.poll_loop())`

테스트:
- 각 명령어 파싱 + 응답 포맷 확인
- 알 수 없는 명령어 → 도움말

## US-118: 전략 간 상관관계 모니터링

구현:
- `engine/src/risk/correlation_monitor.py`: `CorrelationMonitor`
  - 30-trade 롤링 윈도우 (deque)
  - 전략 쌍 PnL Pearson 상관계수 계산
  - 상관계수 > 0.7 → `PositionScaleEvent(strategy_id, scale=0.5)` 반환
  - Prometheus: `strategy_correlation_gauge` (전략 쌍 labels)
- `guardian.py`에 `CorrelationMonitor` 호출 포인트 추가 (check #9)

테스트:
- 상관계수 계산 정확성 (완전 상관 → 1.0)
- 임계값 0.7 초과 → 축소 이벤트
- 윈도우 < 30 → 계산 스킵

## US-119: IOC 주문 타입

구현:
- `engine/src/execution/atomic.py`: `AtomicOrderExecutor`
  - `execute_ioc(exchange, symbol, side, price, size)` → IOC 리밋 주문
  - 부분 체결/타임아웃 → 마켓 오더 폴백
  - 체결 품질 메트릭: IOC vs 마켓 평균 슬리피지 비교
  - Prometheus: `ioc_fill_rate_gauge`, `ioc_vs_market_slippage_histogram`

테스트:
- IOC 완전 체결 시나리오
- 부분 체결 → 마켓 폴백 시나리오
- 체결 품질 메트릭 기록 확인

## US-116: TCA 모듈 + 위젯

구현:
- `engine/src/analysis/tca.py`: `TCAAnalyzer`
  - Implementation shortfall 계산: `(actual_fill - expected_price) / expected_price`
  - P50/P95/P99 실행 레이턴시 집계 (rolling window)
  - fill_rate 추적
  - API 엔드포인트: `GET /api/v1/tca/summary`
- `dashboard/src/components/TCAWidget.tsx`: System 탭 위젯

## US-120: 인벤토리 리밸런싱

구현:
- `engine/src/execution/inventory_rebalancer.py`: `InventoryRebalancer`
  - 드리프트 임계값 (default 5%) 초과 감지
  - 텔레그램 알람 연동
  - 주기적 체크 (60s 간격)
- `main.py`에 리밸런서 등록

---

## 실행 순서

1. **Batch 1 Phase B**: TeamCreate → Jennie(5 US 엔진) + Lisa(테스트) → pytest PASS → TeamDelete
2. **Batch 1 Phase C**: Shadow + Review → SSOT → git push
3. **Batch 2 Phase B**: TeamCreate → Jennie(TCA 엔진) + Rosé(TCA 위젯) + Lisa → pytest + build PASS → TeamDelete
4. **Batch 2 Phase C**: Shadow + Review → SSOT → git push
5. **Batch 3 Phase B/C**: US-120 구현 + 검증 → SSOT → git push
