# Wonyoung — US-297 + US-298 테스트 결과 (Phase S21)

Date: 2026-03-22

## Summary

**Tests Written**: 22 (7 for US-297, 15 for US-298)
**All new tests**: 22 passed, 0 failed
**Full suite**: 5205 passed, 1 failed (pre-existing), 12 skipped

## Test Files

### US-297 — `tests/unit/strategies/test_stat_arb_disable.py`

7 tests covering:

| Test | Behavior |
|------|----------|
| `TestStrategyParamsJson::test_statistical_arb_status_is_disabled` | strategy_params.json에서 stat_arb.status == "DISABLED" 확인 |
| `TestStrategyParamsJson::test_statistical_arb_wfe_is_negative` | stat_arb.wfe == -1.03 확인 |
| `TestStrategyParamsJson::test_other_strategies_retain_their_status` | 다른 전략(spot_futures, funding_rate, cross_exchange) READY/MONITOR 유지 확인 |
| `TestMainStatArbRegistration::test_stat_arb_not_registered_when_disabled` | status=DISABLED → StatisticalArbStrategy 등록 안 됨 |
| `TestMainStatArbRegistration::test_stat_arb_registered_when_ready` | status=READY → StatisticalArbStrategy 등록됨 |
| `TestMainStatArbRegistration::test_stat_arb_registered_when_monitor` | status=MONITOR → StatisticalArbStrategy 등록됨 |
| `TestMainStatArbRegistration::test_other_strategies_always_registered_regardless_of_stat_arb` | stat_arb 비활성화가 CrossExchangeStrategy 등록에 영향 없음 |

### US-298 — `tests/unit/tuning/test_scheduled_tuner_real_data.py`

15 tests covering:

| Test | Behavior |
|------|----------|
| `TestInsufficientDataError::test_class_exists_in_module` | InsufficientDataError 클래스 importable |
| `TestInsufficientDataError::test_is_subclass_of_runtime_error` | RuntimeError 서브클래스 |
| `TestInsufficientDataError::test_can_be_raised_and_caught` | raise/catch 정상 동작 |
| `TestCheckSufficientRealData::test_raises_when_database_url_not_set` | DATABASE_URL 없으면 즉시 InsufficientDataError |
| `TestCheckSufficientRealData::test_raises_when_row_count_below_min` | ohlcv.length < 72 → InsufficientDataError |
| `TestCheckSufficientRealData::test_does_not_raise_when_row_count_meets_minimum` | ohlcv.length >= 72 → 정상 통과 |
| `TestCheckSufficientRealData::test_custom_min_rows_env_var_respected` | TUNER_MIN_REAL_DATA_ROWS env var 반영 |
| `TestOptimizeStrategyInsufficientData::test_returns_insufficient_data_status_on_error` | InsufficientDataError → status=INSUFFICIENT_DATA 반환 |
| `TestOptimizeStrategyInsufficientData::test_returns_error_message_on_insufficient_data` | 에러 메시지 포함 확인 |
| `TestOptimizeStrategyInsufficientData::test_returns_data_type_real_timescaledb_on_insufficient_data` | data_type=real_timescaledb 포함 확인 |
| `TestOptimizeStrategyInsufficientData::test_check_not_called_when_data_source_is_synthetic` | synthetic 소스에서 preflight check 호출 안 됨 |
| `TestDataSourceRouting::test_timescaledb_data_source_stored_on_tuner` | constructor arg data_source=timescaledb 저장 |
| `TestDataSourceRouting::test_timescaledb_data_source_read_from_env` | TUNER_DATA_SOURCE env var 반영 |
| `TestDataSourceRouting::test_synthetic_is_default_data_source` | 기본값 synthetic |
| `TestDataSourceRouting::test_timescaledb_path_calls_check_before_optuna` | preflight check가 Optuna study 생성 전에 실행됨 |

## Verification

```
$ python -m pytest tests/unit/strategies/test_stat_arb_disable.py \
    tests/unit/tuning/test_scheduled_tuner_real_data.py -v --no-cov
22 passed in 1.52s
```

```
$ python -m pytest tests/ -x --tb=short -q --no-cov
FAILED tests/unit/test_strategy_validation.py::TestShadowModeNewMethods::test_get_strategy_report
1 failed (pre-existing, unrelated to US-297/298), 5205 passed, 12 skipped in 268.96s
```

Pre-existing failure root cause: `test_get_strategy_report` uses a `SimpleNamespace` mock that
lacks the `pnl_history` attribute required by `shadow.py::get_strategy_report()` (US-299 method).
This failure exists identically without our new test files (5182 passed without our files,
same 1 failure).

## Notes

- `ThreadPoolExecutor` is imported locally inside `_check_sufficient_real_data` (not at module
  level), so patched via `concurrent.futures.ThreadPoolExecutor` rather than the module attribute.
- All tests follow the existing project pattern: pytest classes, monkeypatch fixtures, `patch.object`
  for Engine internals.
