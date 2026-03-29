# US-298: 실데이터 WFE 백테스트 — 구현 기록

## 변경 파일

### 1. `engine/src/tuning/scheduled_tuner.py`
- **L50-51**: `InsufficientDataError(RuntimeError)` 클래스 추가 — TimescaleDB 행 수 부족 시 발생
- **L148-160**: `_optimize_strategy()` 내 pre-flight 데이터 충분성 검사 추가
  - `data_source == "timescaledb"` 시 `_check_sufficient_real_data()` 호출
  - `InsufficientDataError` 포착 → `{"status": "INSUFFICIENT_DATA", "error": ..., "data_type": "real_timescaledb"}` 반환
- **L193-196**: `_optimize_strategy()` 반환값에 `data_type` 필드 추가
  - timescaledb: `"real_timescaledb"`, synthetic: `"synthetic_gbm"`
- **L237-270**: `_check_sufficient_real_data()` 새 메서드 추가
  - `DATABASE_URL` 미설정 시 `InsufficientDataError` 발생
  - `TUNER_MIN_REAL_DATA_ROWS` 환경변수 (기본값 72 = 3일 × 24시간)
  - 30일치 execution_log 로드 후 행 수 < MIN_ROWS 시 `InsufficientDataError` 발생
- **L262-290**: `_write_params()` 에 `data_type` 기록 + `_real_wfe` 섹션 업데이트
  - READY 전략: `entry["data_type"]` 포함
  - real_timescaledb 결과 전체를 `_real_wfe` 섹션에 기록 (READY + INSUFFICIENT_DATA 모두)

### 2. `engine/config/strategy_params.json`
- `_meta` 에 `real_wfe_path`, `real_wfe_note` 필드 추가
- `_real_wfe` 섹션 추가 — 실데이터 WFE 결과 기록 경로 (ScheduledTuner가 자동 업데이트)

## AC 검증

| AC | 상태 |
|----|------|
| TUNER_DATA_SOURCE=timescaledb 설정 시 실데이터 로드 경로 동작 | PASS — `_check_sufficient_real_data` → `_run_with_timescaledb` 경로 활성 |
| 데이터 부족 시 INSUFFICIENT_DATA 에러 처리 | PASS — `InsufficientDataError` → `{"status": "INSUFFICIENT_DATA"}` 반환 |
| strategy_params.json에 real data WFE 결과 기록 경로 | PASS — `_real_wfe` 섹션 + `_write_params` 자동 기록 |

## 설계 결정

- `_check_sufficient_real_data()` 가 pre-flight 역할 (Optuna 시작 전 1회 DB 조회)
  - Optuna study 시작 전 데이터 부족 확인 → 100 trial 낭비 방지
- `_run_with_timescaledb()` 는 기존 10-row 하한 유지 (기존 테스트 호환성)
  - 실제 3일 가드는 pre-flight 에서 담당
- `TUNER_MIN_REAL_DATA_ROWS` 환경변수로 최소 행 수 재정의 가능 (기본 72)

## 결과: PASS
