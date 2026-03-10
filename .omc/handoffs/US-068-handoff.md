# US-068 Handoff: Shadow 기반 전략 파라미터 재최적화

> **Plan**: `docs/planning/US-068_PLAN.md`
> **Phase**: G (전략 수익성 복원) | **상태**: 기획 완료 → 개발팀 핸드오프

---

## 수정 대상 파일

### 1. `engine/src/tuning/strategy_backtest.py` (수정)
- `STRATEGY_TYPES`에 `"latency_arb"` 추가
- `_make_latency_arb_signals()` 함수 추가 (latency-driven spread, 25% injection, 5-15bps)
- `_build_strategy()` 에 `latency_arb` 케이스 (MockLatencyTracker 사용)
- `_SIGNAL_GENERATORS["latency_arb"]` 매핑

### 2. `engine/src/tuning/scheduled_tuner.py` (수정)
**(A) US-067 activation 필터**:
- `config/strategy_activation.json` 읽어 active_strategies 필터링
- `cex_dex`, `statistical_arb` 무조건 제외

**(B) TimescaleDB 데이터 소스**:
- `data_source` 파라미터: `"synthetic"` (기본) / `"timescaledb"`
- DB 실패 시 synthetic fallback

**(C) ShadowRunner auto-apply**:
- Optuna 후 ShadowRunner.evaluate_and_decide() → APPLY/REJECT 결정
- `_update_strategy_params()` 메서드 추가

### 3. `engine/src/tuning/param_bridge.py` (수정)
- `max_position_usdt` → `max_position_size_usdt` 통일 (5개 전략)

### 4. `engine/config/strategy_params.json` (수정)
- `latency_arb` 섹션 추가:
```json
"latency_arb": {
    "status": "MONITOR",
    "wfe": 0.0,
    "min_spread_bps": 3,
    "max_position_size_usdt": 5000,
    "entry_threshold": 0.0005,
    "exit_threshold": 0.0001,
    "stop_loss_pct": 0.005
}
```

### 5. `engine/tests/unit/tuning/test_scheduled_tuner.py` (확장)
8개 테스트: activation_filter, cex_dex_excluded, stat_arb_excluded, timescaledb_fallback, shadow_runner_auto_apply, strategy_params_updated, wfe_positive_filter, latency_arb_in_types

### 6. `engine/tests/unit/tuning/test_latency_arb_backtest.py` (새 파일)
4개 테스트: signals_count, strategy_integration, produces_trades, different_params

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TUNER_N_TRIALS` | `100` | Optuna trial 수 |
| `TUNER_DATA_SOURCE` | `synthetic` | `synthetic` / `timescaledb` |
| `STRATEGY_ACTIVATION_PATH` | `config/strategy_activation.json` | US-067 결과 |

---

## 의존성
- [x] US-066: StaleOrderbookDetector 4계층 방어
- [x] US-067: StrategyValidationOrchestrator → strategy_activation.json
- [x] ScheduledTuner / WalkForwardOptimizer / StrategyBacktestEngine (Phase E-2)
- [x] ShadowRunner (Phase E-2 US-046)
- [x] LatencyArbStrategy + LatencyTracker 존재

## 핵심 주의사항
1. **이중 슬리피지 금지**: PowerLaw k=0.0 유지
2. **cex_dex 제외**: DEX stub
3. **statistical_arb 제외**: WFE=-1.03
4. **MIN_EDGE_BPS=5 유지**: strategy_params.json의 min_spread_bps와 별도
5. **Docker 필수**: TimescaleDB 사용 시 `docker compose up -d`
