# US-046: Shadow Runner 자동 적용 + TimescaleDB 데이터

## Acceptance Criteria
1. APPLY → config/strategy_params.json 자동 업데이트
2. MONITOR → 다음 주 재검증
3. REJECT → 기존 파라미터 유지 + 알림
4. 최근 7일 execution_log → OHLCV/spread 데이터 변환

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/tuning/shadow_runner.py | EDIT — apply_decision, _apply_params, _mark_for_monitoring | Jennie |
| engine/src/tuning/data_loader.py | EDIT — load_execution_log_as_ohlcv, load_execution_spreads | Jennie |
| engine/tests/unit/tuning/test_shadow_runner_apply.py | NEW — 테스트 | Lisa |
