# US-054 Code Review — Progressive Shadow (1H→2H→6H→12H→24H→72H)

**Date**: 2026-03-10
**Reviewers**: minji (code-reviewer/opus), hanni (critic/opus)
**Status**: APPROVED (after fixes)

## Files Reviewed (5)

1. `engine/src/modes/progressive_shadow.py` (신규 ~620줄)
2. `engine/src/main.py` (~97줄 추가)
3. `docker-compose.yml` (+3줄)
4. `engine/pyproject.toml` (+1줄)
5. `engine/tests/test_progressive_shadow.py` (42 테스트)

## Issues Found & Fixed

### CRITICAL (1)
- **`_db_pool.acquire()` AttributeError**: `DatabasePool` wrapper는 `.acquire()` 없음. `_db_pool.pool.acquire()` 또는 `hasattr` 분기로 수정.

### HIGH (3)
1. **Stage 3 gate tautology**: `active_keys`가 `by_strategy`에서 파생 → `missing` 항상 빈 set. `len(active_keys) >= 2` 실질 검증으로 변경.
2. **`int(os.getenv())` crash**: malformed env var → `ValueError` at import time. `_safe_int()` helper 추가.
3. **DB migration 누락**: `shadow_stage_results` 테이블 CREATE TABLE 없음. `003_shadow_stage_results.sql` 추가.

### MEDIUM (4, 모두 수정)
1. **Double-stop on fail-fast**: `return` → `break` 변경. stop() 1회만 호출 (finally).
2. **Sharpe population variance**: `N` → `N-1` (Bessel's correction).
3. **`_pnl_snapshots` unbounded**: `list` → `deque(maxlen=168)`.
4. **Post-finally unreachable code**: fail-fast path에서 Telegram/STAGE_GAUGE 도달 가능하도록 구조 수정.

### LOW (2, non-blocking)
1. `docker-compose.yml` legacy `mem_limit` syntax (현재 Docker Compose V2 지원)
2. Test `mock_psutil` fixture outer `with MagicMock()` no-op (cosmetic)

## Shadow 10min Test Results

| 항목 | 값 |
|------|-----|
| 거래 수 | 580 (rejected 26건) |
| 승률 | 67.2% (390W / 190L) |
| PnL | +$2,203.92 |
| Max DD | $342.62 (15.47%) |
| Crash | 0 |
| 활성 전략 | 2 (spot_futures_v1, cross_exchange_v1) |
| 거래소 | 8/8 연결 |

## Critic Assessment

- **Wrapper/Decorator 패턴**: APPROVED. ShadowMode 1,567줄 무변경, SRP 준수.
- **6-stage gate 설계**: APPROVED (Stage 3 수정 후). 현실적 임계값.
- **Fail-fast**: APPROVED. 재시도 복잡도는 72H 안정성 확인 후 도입.
- **리소스 모니터링**: psutil fallback 0 반환 시 WARNING 로그 권장 (MEDIUM, deferred).

## Verdict: APPROVED
