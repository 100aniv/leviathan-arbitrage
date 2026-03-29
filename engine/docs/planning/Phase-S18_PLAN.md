# Phase S18 PLAN — 포트폴리오 리스크 + 평가 체계 + Slippage Feedback

**Phase**: S18
**US 범위**: US-277 ~ US-285 (11 US)
**날짜**: 2026-03-20
**복잡도**: MEDIUM-HIGH (신규 portfolio_risk.py + dead code 활성화 2건 + Slippage 피드백 루프)

---

## 1. Context

Phase S17까지 전략별 고급 기능과 실행 안전장치가 완비되었으나, 포트폴리오 수준의 리스크 관리 레이어가 부재. 핵심 문제:

- `capital_allocator.py` (156 LOC, Kelly Criterion 완성) — 한 번도 import되지 않은 dead code
- `attribution.py` (226 LOC, PnL 분해 완성) — EngineContext에 미주입, 호출 경로 없음
- `EngineContext.slippage_feedback` 필드 존재하나 DB 기록 및 피드백 루프 미구현
- 포트폴리오 수준 상관행렬·VaR·MDD 제한 없음 → 전략 집중 리스크 방치

---

## 2. Work Objectives

1. **Wiring 우선**: dead code 2건(CapitalAllocator, Attribution) EngineContext에 연결
2. **portfolio_risk.py 신규**: 상관행렬, VaR(95/99%), Portfolio MDD 계산
3. **MDD 경계**: 전략별 3% / 포트폴리오 5% 한도 초과 시 포지션 축소
4. **Regime-Aware 배분**: RegimeDetector 출력 → Kelly 가중치 조정
5. **LiveGate 상시화**: Shadow 종료 후에도 LiveGate Enforcer 상시 동작
6. **실시간 성과지표**: Sharpe / Calmar / Sortino 롤링 계산
7. **Slippage Feedback Loop**: 실제 슬리피지 DB 기록 → 예측 모델 보정
8. **Market Impact Cost 모델**: 대형 주문 시장충격 추정
9. **Shadow 10min 통합 검증**

---

## 3. Guardrails

### Must Have
- `CapitalAllocator` / `PerformanceAttribution` import 경로 확인 후 wiring (기존 코드 수정 최소화)
- 모든 신규 컴포넌트에 env var ON/OFF 토글 (`PORTFOLIO_RISK_ENABLED`, `SLIPPAGE_FEEDBACK_ENABLED`)
- 기존 테스트 전량 통과 (regression 0)
- 각 US별 최소 3개 단위테스트
- WIRING AC 3개 (생성 → 주입 → 호출) 검증

### Must NOT Have
- `portfolio_risk.py`에 pandas 이상의 중량 의존성 (numpy만 허용, scipy는 optional)
- `CapitalAllocator` 활성화가 기존 `DynamicSizer` 로직에 간섭
- DB 스키마 신규 테이블 추가 시 migration_runner 없이 직접 DDL 실행
- PaperExecutor에 Market Impact Cost 이중 적용 (SignalGenerator 슬리피지와 합산 금지)

---

## 4. 의존성 분석 (배치 순서 근거)

```
US-284-b (Attribution wiring)  ─┐
US-284-a (CapitalAllocator wiring)─┤  Batch 1: 기존 dead code 활성화 (wiring only)
                                   │
US-277 (portfolio_risk.py 신규) ──┤  Batch 2: portfolio_risk.py가 US-278/279의 기반
US-278 (MDD 관리)  ─────────────┤    US-278은 US-277의 MDD 계산에 의존
US-279 (Regime-Aware 배분) ──────┘    US-279는 CapitalAllocator(US-284-a) + Regime 의존

US-280 (LiveGate Enforcer)  ─┐
US-281 (실시간 지표)  ────────┤  Batch 3: 평가 체계 (서로 독립, 병렬 가능)
US-282 (Attribution 분석) ───┘    US-282는 US-284-b wiring 완료 후 진행

US-283 (Slippage Feedback) ──┐
US-284 (Market Impact) ──────┘  Batch 4: 실행 모델 (서로 독립, 병렬 가능)

US-285 (Shadow 통합) ──────────  Batch 5: 최종 통합 검증
```

---

## 5. Batch 상세

### Batch 1 — Wiring (순차, ~2h)

#### US-284-b: Attribution EngineContext 연결

**대상 파일**:
- `engine/src/analysis/attribution.py` — 수정 없음 (코드 완성)
- `engine/src/core/engine.py` — `PerformanceAttribution` import + 인스턴스 생성 + `on_fill` 연결
- `engine/src/core/models.py` — `EngineContext`에 `attribution: PerformanceAttribution | None` 필드 추가

**⚡ WIRING AC**:
1. `engine.py`에서 `PerformanceAttribution()` 인스턴스 생성 확인
2. `EngineContext.attribution` 필드에 주입 확인
3. `on_fill()` 핸들러에서 `attribution.add_trade()` 호출 확인

**Acceptance Criteria**:
- `PerformanceAttribution` import 오류 없음
- `engine.py` 기동 시 `attribution` 인스턴스 로깅 확인
- `on_fill` 이벤트 발생 시 `attribution._trades` 증가 확인 (단위테스트)

---

#### US-284-a: CapitalAllocator EngineContext 연결

**대상 파일**:
- `engine/src/core/capital_allocator.py` — 수정 없음 (코드 완성)
- `engine/src/core/engine.py` — `CapitalAllocator` import + 인스턴스 생성
- `engine/src/core/models.py` — `EngineContext`에 `capital_allocator: CapitalAllocator | None` 필드
- `engine/src/core/dynamic_sizer.py` — `CapitalAllocator.allocate()` 결과를 사이즈 상한으로 활용 (선택적 override)

**⚡ WIRING AC**:
1. `engine.py`에서 `CapitalAllocator(total_capital=...)` 생성 확인
2. `EngineContext.capital_allocator` 필드에 주입 확인
3. `DynamicSizer` 또는 `SignalGenerator`에서 `allocate()` 호출 확인

**Acceptance Criteria**:
- `CapitalAllocator` 인스턴스 생성 시 `config/capital_allocation.json` 없어도 기본값으로 동작
- `allocate()` 호출 결과가 포지션 사이즈 상한으로 반영 (단위테스트)

---

### Batch 2 — Portfolio 핵심 (순차, ~4h)

#### US-277: portfolio_risk.py 신규 (상관행렬, VaR)

**신규 파일**: `engine/src/core/portfolio_risk.py`

**구현 내용**:
```python
class PortfolioRiskManager:
    # 상관행렬: 전략별 수익률 시계열로 pearson correlation
    # VaR: Historical VaR (95%, 99%) — numpy percentile 기반
    # Portfolio Volatility: sqrt(w^T * Σ * w)
    # 인터페이스: update_returns(strategy_id, pnl) / get_var(confidence=0.95) / get_correlation_matrix()
```

**의존성**: numpy only (scipy.stats optional for parametric VaR)

**⚡ WIRING AC**:
1. `engine/src/core/portfolio_risk.py` 파일 생성 + `PortfolioRiskManager` 클래스 정의 확인
2. `EngineContext`에 `portfolio_risk: PortfolioRiskManager | None` 필드 주입 확인
3. `engine.py`의 `on_fill()` 핸들러에서 `portfolio_risk.update_returns()` 호출 확인

**Acceptance Criteria**:
- `get_var(0.95)` 호출 시 수익률 시계열 < 20개이면 `None` 반환 (데이터 부족 보호)
- 상관행렬 symmetric 검증 (단위테스트)
- `PORTFOLIO_RISK_ENABLED=false`이면 인스턴스 생성 스킵

---

#### US-278: 포트폴리오 MDD 관리 (3%/5%)

**대상 파일**:
- `engine/src/core/portfolio_risk.py` — `check_mdd_breach(strategy_id)` 메서드 추가
- `engine/src/core/risk_guardian.py` — MDD 초과 시 전략 일시 정지 훅 연결

**MDD 기준**:
- 전략별 MDD > 3% → 해당 전략 포지션 신규 진입 차단 (기존 포지션 유지)
- 포트폴리오 MDD > 5% → 전체 신규 진입 차단

**Acceptance Criteria**:
- MDD 계산식: `(peak - current) / peak * 100`
- `risk_guardian.py`의 `can_trade(strategy_id)` 반환값이 MDD 초과 시 `False`
- 단위테스트: MDD 3.1% 시나리오에서 해당 전략 차단 확인

---

#### US-279: Regime-Aware 자본 배분

**대상 파일**:
- `engine/src/core/capital_allocator.py` — `allocate_with_regime(regime: str)` 메서드 추가
- `engine/src/core/engine.py` — RegimeDetector 출력 → `allocate_with_regime()` 연결

**Regime 가중치**:
| Regime | Kelly 승수 |
|--------|-----------|
| `bull` | 1.0 (full) |
| `neutral` | 0.7 |
| `bear` | 0.4 |
| `crisis` | 0.1 |

**Acceptance Criteria**:
- Regime `bear`에서 Kelly fraction 40%로 축소 확인 (단위테스트)
- `REGIME_AWARE_ALLOCATION_ENABLED=false`이면 기존 Kelly 100% 사용

---

### Batch 3 — Evaluation (병렬 가능, ~3h)

#### US-280: LiveGate Enforcer 상시화

**대상 파일**:
- `engine/src/core/live_gate.py` — `start_continuous_monitor(interval_s=60)` 메서드 추가
- `engine/src/core/engine.py` — Shadow 모드 이외에서도 LiveGate 주기 평가 활성화

**Acceptance Criteria**:
- `ENGINE_ENV=prod`에서 60초마다 LiveGate 6-check 수행 로그 확인
- LiveGate 실패 시 `risk_guardian`의 emergency stop 호출 (단위테스트 mock)
- `LIVE_GATE_CONTINUOUS_ENABLED=false` 토글 동작 확인

---

#### US-281: Sharpe/Calmar/Sortino 실시간 계산

**대상 파일**:
- `engine/src/core/metrics_collector.py` — 롤링 지표 계산 메서드 추가

**구현 내용**:
- Rolling window: 최근 500개 trade
- Sharpe: `mean(r) / std(r) * sqrt(annualization_factor)`
- Sortino: `mean(r) / std(r[r<0]) * sqrt(factor)` (하방 편차만)
- Calmar: `annualized_return / max_drawdown`
- 노출: `GET /api/v1/metrics/portfolio` 엔드포인트

**Acceptance Criteria**:
- 수익률 0개이면 모두 `0.0` 반환 (ZeroDivision 방어)
- 단위테스트: 알려진 수익률 시퀀스로 Sharpe 수치 검증 (허용오차 1e-6)

---

#### US-282: 전략별 Attribution 분석

**대상 파일**:
- `engine/src/analysis/attribution.py` — `get_report()` 메서드 추가 (JSON 직렬화 가능)
- `engine/src/api/routes/metrics.py` — `GET /api/v1/attribution` 엔드포인트 추가

**Acceptance Criteria**:
- US-284-b wiring 완료 후 진행
- `get_report()` 반환값: `{"by_strategy": {...}, "by_exchange": {...}, "by_pair": {...}}`
- 빈 trades 목록에서 빈 dict 반환 (에러 없음)

---

### Batch 4 — Execution 모델 (병렬 가능, ~3h)

#### US-283: Slippage Feedback Loop

**대상 파일**:
- `engine/src/core/models.py` — `SlippageFeedbackRecord` dataclass 신규
- `engine/src/execution/paper_executor.py` — 실제 체결가 vs 예상가 차이 기록
- `engine/src/db/slippage_feedback_repo.py` — TimescaleDB INSERT (hypertable: `slippage_feedback`)
- `engine/src/core/signal.py` — `SignalGenerator`에서 피드백 기반 슬리피지 예측값 보정

**DB 스키마** (`migration_runner` 경유 필수):
```sql
CREATE TABLE IF NOT EXISTS slippage_feedback (
    time        TIMESTAMPTZ NOT NULL,
    exchange    TEXT,
    pair        TEXT,
    predicted   DOUBLE PRECISION,
    actual      DOUBLE PRECISION,
    delta_bps   DOUBLE PRECISION
);
SELECT create_hypertable('slippage_feedback', 'time', if_not_exists => TRUE);
```

**⚡ WIRING AC**:
1. `SlippageFeedbackRecord` 정의 확인 (models.py)
2. `paper_executor.on_fill()` 에서 `slippage_feedback_repo.insert()` 호출 확인
3. `SignalGenerator`에서 최근 100건 평균 delta_bps로 슬리피지 예측 보정 확인

**Acceptance Criteria**:
- `SLIPPAGE_FEEDBACK_ENABLED=false`이면 DB INSERT 스킵
- 단위테스트: mock DB로 insert/query 사이클 검증
- 피드백 보정 후 예상 슬리피지가 ±50% 이상 변화하지 않도록 클램핑 (`[0.5x, 2.0x]`)

---

#### US-284: Market Impact Cost 모델

**대상 파일**:
- `engine/src/core/signal.py` — `estimate_market_impact(size_usd, daily_volume_usd)` 함수 추가

**수식**: Almgren-Chriss 선형 근사
```
impact_bps = eta * (size_usd / daily_volume_usd) * 10_000
eta = 0.1  # 보수적 선형 계수 (env var MARKET_IMPACT_ETA로 조정 가능)
```

**Acceptance Criteria**:
- `daily_volume_usd = 0` → `impact_bps = 0.0` (ZeroDivision 방어)
- `size_usd / daily_volume_usd > 0.01` (1% 이상) → 경고 로그
- PaperExecutor에 직접 적용 금지 (SignalGenerator 단에서만 필터링에 사용)
- 단위테스트 3개: 소형/중형/대형 주문 impact 수치 검증

---

### Batch 5 — Shadow 통합 검증 (순차, ~2h)

#### US-285: S18 통합 Shadow 10min

**선행 조건**: Batch 1~4 전량 완료 + 기존 테스트 통과

**검증 항목** (Shadow 13항목 기준):

| # | 항목 | 기준 |
|---|------|------|
| 1 | PnL | > 0 |
| 2 | MDD | < 5% |
| 3 | Crash | 0건 |
| 4 | CapitalAllocator 호출 | 로그 확인 |
| 5 | Attribution 집계 | `by_strategy` 비어있지 않음 |
| 6 | Portfolio VaR 계산 | `None` 아닌 값 반환 |
| 7 | LiveGate 주기 평가 | 60초 로그 확인 |
| 8 | Sharpe 계산 | 0이 아닌 값 |
| 9 | Slippage Feedback DB | 최소 1건 INSERT |
| 10 | Market Impact 계산 | 경고 로그 없음 (주문 < 1%) |
| 11 | Regime-Aware 배분 | Kelly 승수 로그 확인 |
| 12 | MDD breach 방어 | 임계치 미초과 확인 |
| 13 | 전략 trade >= 1 | cross_exchange 최소 1건 |

**Acceptance Criteria**:
- Shadow 10min 완료 후 위 13항목 모두 PASS
- `engine/tests/unit/strategies/test_s18_portfolio.py` 파일 생성 + 최소 15개 테스트

---

## 6. 테스트 전략

### 신규 테스트 파일
| 파일 | 대상 US | 최소 테스트 수 |
|------|---------|--------------|
| `tests/unit/core/test_portfolio_risk.py` | US-277, US-278 | 8개 |
| `tests/unit/core/test_capital_allocator_wiring.py` | US-284-a, US-279 | 6개 |
| `tests/unit/analysis/test_attribution_wiring.py` | US-284-b, US-282 | 5개 |
| `tests/unit/core/test_live_gate_continuous.py` | US-280 | 4개 |
| `tests/unit/core/test_metrics_rolling.py` | US-281 | 5개 |
| `tests/unit/execution/test_slippage_feedback.py` | US-283 | 5개 |
| `tests/unit/core/test_market_impact.py` | US-284 | 3개 |
| `tests/unit/strategies/test_s18_portfolio.py` | US-285 통합 | 15개 |

**목표**: 신규 테스트 51개 이상 추가, 기존 4,962개 전량 통과

---

## 7. 리스크 + 주의사항

| 리스크 | 확률 | 완화 방안 |
|--------|------|----------|
| `EngineContext` 필드 추가 시 기존 초기화 코드 누락 | 중 | `models.py` 수정 후 전체 grep으로 `EngineContext(` 생성자 확인 |
| `capital_allocator.json` 없는 환경 KeyError | 중 | 기본값 fallback 이미 구현됨, 단위테스트로 검증 |
| Portfolio VaR 20개 미만 데이터 시 오동작 | 저 | `None` 반환 + 호출부 guard 필수 |
| Slippage Feedback DB 스키마 migration 누락 | 고 | migration_runner에 DDL 등록 후 `check_all` 실행 필수 |
| Market Impact Cost + Slippage 이중 적용 | 중 | PaperExecutor에 직접 추가 금지 (Guardrail 참조) |

---

## 8. 완료 기준 (Phase S18 Done)

- [ ] 단위테스트 51개 이상 신규 추가 + 기존 4,962개 전량 통과
- [ ] `CapitalAllocator` / `PerformanceAttribution` 런타임 호출 로그 확인 (dead code 해소)
- [ ] `portfolio_risk.py` VaR + MDD 수치 Shadow 로그에서 확인
- [ ] Slippage Feedback DB INSERT 최소 1건 확인
- [ ] Shadow 10min 13항목 전량 PASS (PnL > 0, MDD < 5%, crash 0)
- [ ] SSOT.md S18 항목 passes:true 업데이트
