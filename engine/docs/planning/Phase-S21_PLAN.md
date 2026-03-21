# Phase S21 — StatArb WFE Fix + Real-Data WFE + Strategy Shadow + Portfolio Shadow

**Status**: PENDING
**Date**: 2026-03-21
**Dependencies**: US-285, US-290-a, US-296 (all passes:true)

---

## Phase 개요

Phase S21은 WFE=-1.03 상태인 stat_arb를 수익성 복원하고, 실 데이터 기반 Walk-Forward
Evaluation을 도입한 뒤, ShadowMode에 전략별 독립 실행과 PortfolioRiskManager 통합을
완료하는 4개 US 묶음이다.

배치 구조:
- **배치 1 (병렬)**: US-297 + US-298 — 독립적, 동시 실행 가능
- **배치 2 (순차)**: US-299 — US-297/298 완료 후
- **배치 3 (순차)**: US-300 — US-299 완료 후

---

## US 목록

### US-297: StatArb WFE 수정 (WFE -1.03 → > 0)

**목표**: `config/strategy_params.json`의 `statistical_arb.wfe=-1.03` 원인을
분석하고 파라미터를 교정하여 WFE > 0 달성.

**현황 분석**:
- `statistical_arb.status = "MONITOR"`, WFE = -1.03 (9/97 fold만 양수)
- 현재 파라미터: entry_threshold=0.000188, exit_threshold=0.00089
- exit > entry 역전 상태 — 전략이 진입 직후 강제 청산됨
- `zscore_entry=2.5`는 실 데이터에서 거의 발동 안 됨

**구현 계획**:
1. `engine/src/tuning/optimizer.py` + `strategy_backtest.py`에서 stat_arb
   WalkForwardOptimizer 실행 (synthetic GBM 대신 mean-reverting OU 프로세스 사용)
2. `StatArbConfig` 파라미터 재조정:
   - `zscore_entry`: 2.5 → 1.8 (신호 발생 빈도 증가)
   - `min_history`: 120 → 60 (빠른 warm-up)
   - `enable_cointegration`: 기본값 유지 (True)
3. `config/strategy_params.json`의 `statistical_arb` 섹션 업데이트:
   - `status`: "MONITOR" → "READY"
   - `wfe`: -1.03 → 최적화 결과값
   - `zscore_entry`, `min_history` 필드 추가

**파일 변경**:
- `engine/config/strategy_params.json` — stat_arb 파라미터 업데이트
- `engine/src/strategies/statistical_arb.py` — 필요 시 OU 프로세스 검증 로직 보완

**테스트 계획**:
- `tests/unit/strategies/test_s17_stat_arb.py` — 기존 통과 유지
- `tests/unit/core/test_ou_process.py` — OU 파라미터 범위 검증
- 신규 테스트: WFE > 0 검증 픽스처 1개

**수용 기준**:
- `strategy_params.json`의 `statistical_arb.wfe > 0`
- `statistical_arb.status == "READY"`
- 기존 stat_arb 단위 테스트 전원 통과

---

### US-298: 실 데이터 WFE 백테스트

**목표**: WalkForwardOptimizer가 TimescaleDB 실 OHLCV 데이터를 사용하도록 확장.
현재는 synthetic GBM만 지원.

**현황 분석**:
- `engine/src/tuning/data_loader.py` — DataLoader 존재, DB 연결 방식 확인 필요
- `ScheduledTuner`의 `data_source` 파라미터로 "real" 전달 시 경로 없음
- `strategy_params.json._meta.data_type = "synthetic_gbm"` — 실 데이터 미사용 명시

**구현 계획**:
1. `DataLoader.load_real(exchange, symbol, start, end)` 메서드 구현:
   - TimescaleDB `ohlcv` 테이블 쿼리
   - DB 미연결 시 synthetic fallback
2. `WalkForwardOptimizer.__init__`에 `data_source: str = "synthetic"` 파라미터 추가:
   - `"real"` 전달 시 DataLoader.load_real() 사용
3. `ScheduledTuner`의 `data_source` 파라미터를 optimizer까지 전파
4. `strategy_params.json._meta.data_type` 을 실행 시점에 기록

**파일 변경**:
- `engine/src/tuning/data_loader.py` — load_real() 추가
- `engine/src/tuning/optimizer.py` — data_source 파라미터 추가
- `engine/src/tuning/scheduled_tuner.py` — data_source 전파

**테스트 계획**:
- `tests/unit/strategies/test_s18_portfolio.py` — 기존 통과 유지
- 신규: DataLoader.load_real() 단위 테스트 (DB mock 사용)
- 신규: WalkForwardOptimizer data_source="real" 경로 커버리지

**수용 기준**:
- `data_source="real"` 전달 시 DataLoader.load_real() 호출 (로그 증거)
- DB 없을 때 synthetic fallback 동작
- 기존 optimizer 단위 테스트 전원 통과

---

### US-299: 전략별 독립 Shadow 30분

**목표**: ShadowMode에 `strategy_filter` 파라미터를 추가하여 전략 1개씩 독립 실행하고
각 전략의 30분 Shadow 결과(trades, WR, PnL, DD)를 수집.

**현황 분석**:
- `ShadowMode.__init__`에 `strategy_filter` 파라미터 없음
- `strategy_manager.list_strategies()` → 전략 ID 목록 확인 가능
- 전략별 독립 실행 없이는 어느 전략이 PnL 기여/손실인지 알 수 없음

**구현 계획**:
1. `ShadowMode.__init__`에 `strategy_filter: list[str] | None = None` 파라미터 추가
2. `ShadowMode.start()` 내부에서 `strategy_manager` 로드 시 filter 적용:
   ```
   if self._strategy_filter:
       active = [s for s in strategies if s in self._strategy_filter]
   ```
3. `main.py`의 `_shadow_mode_loop()`에서 `strategy_filter` 주입 지원
   (환경변수 `SHADOW_STRATEGY_FILTER=stat_arb,cross_exchange` 파싱)
4. 전략별 Shadow 결과 요약 로그 추가 (`strategy_id`, `trades`, `pnl`, `win_rate`, `mdd`)

**파일 변경**:
- `engine/src/modes/shadow.py` — strategy_filter 파라미터 + 필터 로직
- `engine/src/main.py` — SHADOW_STRATEGY_FILTER env 파싱 및 주입

**WIRING AC (US-299)**:
- AC1 (생성): `ShadowMode.__init__`에 `strategy_filter` 파라미터 존재 (`shadow.py` 라인 ~382)
- AC2 (주입): `main.py._shadow_mode_loop()`에서 `ShadowMode(strategy_filter=filter_list)` 전달
- AC3 (호출): Shadow 실행 중 `strategy_manager.list_strategies()` 결과가 filter 기준으로
  제한됨 — 로그에 `"strategy_filter_active": ["stat_arb"]` 출력

**테스트 계획**:
- 신규: `tests/unit/core/test_shadow_strategy_filter.py`
  - strategy_filter=["stat_arb"] 시 다른 전략 비활성 검증
  - strategy_filter=None 시 전략 전체 활성 유지 검증
- 기존 shadow 관련 테스트 전원 통과

**수용 기준**:
- `SHADOW_STRATEGY_FILTER=stat_arb` 설정 시 stat_arb만 실행되는 로그 증거
- 30분 실행 후 전략별 trades >= 1 (stat_arb 최소 1건)
- 기존 ShadowMode 단위 테스트 전원 통과

---

### US-300: 포트폴리오 통합 Shadow 1시간

**목표**: `PortfolioRiskManager`를 ShadowMode에 주입하여 1시간 통합 Shadow 실행 중
포트폴리오 상관관계, VaR, 전략별 MDD를 수집 및 검증.

**현황 분석**:
- `PortfolioRiskManager` (`engine/src/core/portfolio_risk.py`) — 구현 완료, 주입 미완
- `ShadowMode.__init__` 현재 서명에 `portfolio_risk_manager` 파라미터 없음
- `main.py`에서 `ShadowMode(...)` 호출 시 `portfolio_risk_manager` 전달 코드 없음
- `PortfolioRiskManager.update_returns(strategy_id, pnl)` — PnL 샘플 수집 인터페이스 존재

**구현 계획**:
1. `ShadowMode.__init__`에 `portfolio_risk_manager: Any | None = None` 파라미터 추가
2. `_execute_shadow_trade()` 완료 후 `portfolio_risk_manager.update_returns()` 호출:
   ```python
   if self._portfolio_risk_manager:
       self._portfolio_risk_manager.update_returns(strategy_id, pnl)
   ```
3. `ShadowMode` 종료 시 `portfolio_risk_manager.get_portfolio_summary()` 호출 후
   Telegram 알림 및 로그 출력
4. `main.py._shadow_mode_loop()`에서 `PortfolioRiskManager` 인스턴스 생성 후 주입:
   ```python
   from src.core.portfolio_risk import PortfolioRiskManager
   _portfolio_risk = PortfolioRiskManager(window_minutes=60)
   self._shadow_mode = ShadowMode(..., portfolio_risk_manager=_portfolio_risk)
   ```

**파일 변경**:
- `engine/src/modes/shadow.py` — portfolio_risk_manager 파라미터 + update_returns 호출
- `engine/src/main.py` — PortfolioRiskManager 생성 및 ShadowMode 주입 (~라인 2392)

**WIRING AC (US-300)**:
- AC1 (생성): `main.py._shadow_mode_loop()`에서 `PortfolioRiskManager(window_minutes=60)` 인스턴스 생성
- AC2 (주입): `ShadowMode.__init__`에 `portfolio_risk_manager=_portfolio_risk` 전달
- AC3 (호출): `_execute_shadow_trade()` 완료 시 `portfolio_risk_manager.update_returns()` 호출 —
  Shadow 1시간 로그에 `"portfolio_var"`, `"strategy_mdd"` 키 포함 출력

**테스트 계획**:
- `tests/unit/core/test_portfolio_risk.py` — 기존 통과 유지
- 신규: `tests/unit/core/test_shadow_portfolio_wiring.py`
  - update_returns() 호출 검증 (mock PortfolioRiskManager)
  - MDD threshold 초과 시 경고 로그 검증
- 기존 ShadowMode 단위 테스트 전원 통과

**수용 기준**:
- 1시간 Shadow 완료 후 `portfolio_risk_manager.get_portfolio_summary()` 출력 로그 존재
- 전략별 MDD 값 수집 (최소 stat_arb + cross_exchange 2개)
- STRATEGY_MDD_THRESHOLD_PCT(3%) 초과 전략 경고 로그 출력
- 기존 portfolio_risk 단위 테스트 전원 통과

---

## 배치 구조

```
배치 1 (병렬 실행)
├── US-297: StatArb WFE 수정
└── US-298: 실 데이터 WFE 백테스트

배치 2 (순차 — 배치 1 완료 후)
└── US-299: 전략별 독립 Shadow 30분

배치 3 (순차 — 배치 2 완료 후)
└── US-300: 포트폴리오 통합 Shadow 1시간
```

---

## 예상 테스트 수

| US | 신규 | 기존 유지 | 합계 |
|----|------|----------|------|
| US-297 | +3 | 기존 stat_arb 전체 | +3 |
| US-298 | +4 | optimizer/tuner 전체 | +4 |
| US-299 | +4 | shadow 관련 전체 | +4 |
| US-300 | +4 | portfolio_risk 전체 | +4 |
| **총계** | **+15** | | |

현재 5,183 tests → 예상 **5,198 tests**

---

## 리스크 + 완화 전략

| 리스크 | 가능성 | 완화 |
|-------|--------|------|
| stat_arb WFE 최적화 후에도 < 0 유지 | 중 | zscore_entry 1.5까지 낮추고 min_zero_crossings=1로 완화 — 여전히 < 0이면 status="DISABLED" 처리 |
| TimescaleDB ohlcv 테이블 비어있음 | 고 | DataLoader.load_real()에 empty-result 감지 + synthetic fallback 필수 |
| ShadowMode strategy_filter가 기존 테스트 깨뜨림 | 저 | filter=None이 기본값이므로 기존 경로 무변경 |
| PortfolioRiskManager 주입 후 성능 저하 | 저 | update_returns()는 O(n) 리스트 append — 30분 윈도우 prune 로직 이미 존재 |

---

## Shadow 실행 기준 (Phase S21 완료 조건)

단위 테스트 통과만으로 Phase 완료 선언 금지.

1. US-299: 각 전략 30분 Shadow — trades >= 1, crash 0건
2. US-300: 통합 1시간 Shadow — PnL > 0, crash 0건, portfolio_summary 로그 존재
3. prd.json US-297~300 모두 passes:true로 업데이트
4. SSOT.md Phase S21 완료 기록
