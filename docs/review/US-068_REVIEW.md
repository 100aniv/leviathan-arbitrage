# US-068 Code Review: Shadow 기반 전략 파라미터 재최적화

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-11
**Files Reviewed**: 11 (5 source + 6 test)
**Total Issues**: 6

---

## By Severity

| Severity | Count | Action |
|----------|-------|--------|
| CRITICAL | 1 | Must fix |
| HIGH     | 1 | Should fix |
| MEDIUM   | 3 | Consider fixing |
| LOW      | 1 | Optional |

---

## Stage 1: Spec Compliance

### Requirements Verified

| Requirement | Status | Evidence |
|-------------|--------|----------|
| latency_arb 튜닝 파이프라인 추가 | PASS | `STRATEGY_TYPES` 에 추가, `_make_latency_arb_signals`, `_MockLatencyTracker`, `_build_strategy` latency_arb 분기, `_SIGNAL_GENERATORS` 등록 |
| activation 필터 + EXCLUDED set | PASS | `ScheduledTuner.__init__` 에서 `strategy_activation.json` 로드 후 `EXCLUDED = {"cex_dex", "statistical_arb"}` 제거 |
| data_source 파라미터 (TimescaleDB fallback) | PASS | `self.data_source` + `_run_with_timescaledb` + synthetic fallback |
| ShadowRunner auto-apply | PASS | `_apply_shadow_decisions` 가 `run_optimization` 끝에서 호출 |
| WFE status gate | PASS | `best_value > 0` 이면 `status = "READY"` 설정 |
| param_bridge 키 정규화 | PARTIAL | `max_position_usdt` -> `max_position_size_usdt` 일괄 변경됨. 단, `main.py:579` 에서 triangular 가 여전히 `max_position_usdt` 키를 읽고 있어 불일치 (아래 CRITICAL 참조) |
| min_spread_bps_range 하한 조정 | PASS | `(1.0, 50.0)` -> `(3.0, 50.0)` |
| strategy_params.json latency_arb 추가 | PASS | status=MONITOR, wfe=0.0, 기본값 설정 |
| 이중 슬리피지 금지 위반 여부 | PASS | PowerLaw 참조 없음. SignalGenerator 경유만 유지 |
| PowerLaw k=0.0 유지 | PASS | 튜닝 모듈에 PowerLaw 참조 전무 |

**Stage 1 Verdict**: PARTIAL PASS -- `strategy_params.json` 의 triangular 키가 `max_position_size_usdt` 로 변경되었으나, `main.py:579` 가 아직 `max_position_usdt` 를 읽고 있어 런타임에 기본값 1000으로 fallback 됨.

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Result |
|------|--------|
| `engine/src/tuning/strategy_backtest.py` | 0 errors, 0 warnings |
| `engine/src/tuning/scheduled_tuner.py` | 0 errors, 0 warnings |
| `engine/src/tuning/param_bridge.py` | 0 errors, 0 warnings |
| `engine/src/tuning/optimizer.py` | 0 errors, 0 warnings |

### Security Check

- No hardcoded secrets, API keys, or tokens found in changed files.
- No SQL injection vectors.
- `json.loads(path.read_text())` in `_load_activation` is safe (local file, not user input).

---

## Issues

### [CRITICAL] strategy_params.json <-> main.py triangular 키 불일치

**File**: `engine/config/strategy_params.json:30` + `engine/src/main.py:579`

**Issue**: `strategy_params.json` 의 triangular 섹션에서 `max_position_usdt` 가 `max_position_size_usdt` 로 이름 변경되었으나, `engine/src/main.py:579` 는 여전히 `tri_p.get("max_position_usdt", 1000)` 을 사용한다. 결과적으로 JSON 에서 읽은 값(100 USDT)이 무시되고 기본값(1000 USDT)이 적용되어 **삼각 차익 전략의 포지션 한도가 10배 증가**한다.

```python
# main.py:579 (현재 -- 구 키로 읽음)
max_position_usdt=Decimal(str(tri_p.get("max_position_usdt", 1000))),

# strategy_params.json (변경 후 -- 신규 키)
"max_position_size_usdt": 100,
```

**Impact**: 삼각 전략이 의도한 100 USDT 대신 1000 USDT 포지션으로 실행. 과대 포지션에 의한 손실 위험.

**Fix**: `engine/src/main.py:579` 를 다음과 같이 수정:

```python
max_position_usdt=Decimal(str(tri_p.get("max_position_size_usdt", tri_p.get("max_position_usdt", 1000)))),
```

또는 정규화 이후 구 키가 존재하지 않으므로:

```python
max_position_usdt=Decimal(str(tri_p.get("max_position_size_usdt", 1000))),
```

---

### [HIGH] _MockLatencyTracker 가 LatencyTracker 인터페이스를 위반 (None 반환 불가)

**File**: `engine/src/tuning/strategy_backtest.py:468-482`

**Issue**: 실제 `LatencyTracker.get_latency_info()` (latency_tracker.py:54)는 알 수 없는 exchange에 대해 `Optional[ExchangeLatencyInfo]` (즉 `None`)을 반환한다. 그러나 `_MockLatencyTracker.get_latency_info()` 는 항상 `ExchangeLatencyInfo` 객체를 반환하며 `None` 을 반환하는 경로가 없다. 이는 두 가지 문제를 초래한다:

1. **타입 불일치**: `LatencyArbStrategy.__init__` 의 타입 힌트는 `latency_tracker: LatencyTracker` 를 요구하지만, `_MockLatencyTracker` 는 이를 상속하지 않으므로 정적 분석 도구에서 경고가 발생할 수 있다.
2. **테스트 커버리지 갭**: mock이 None을 반환하지 않으므로, latency_arb 백테스트가 알 수 없는 exchange에 대한 graceful degradation 경로를 테스트하지 않는다.

**Impact**: 백테스트 결과가 실 환경보다 낙관적일 수 있음 (실환경에서는 latency 데이터가 없는 exchange에 대해 필터링됨).

**Fix**: `_MockLatencyTracker` 를 `LatencyTracker` 를 상속하거나, 최소한 반환 타입을 `Optional[ExchangeLatencyInfo]` 로 명시하고 unknown exchange에 대해 `None` 반환 경로를 추가:

```python
class _MockLatencyTracker(LatencyTracker):
    def __init__(self):
        super().__init__()
        # Pre-seed known exchanges
        self.record_latency("binance", 10.0)
        for _ in range(49):
            self.record_latency("binance", 10.0)
        self.record_latency("bybit", 50.0)
        for _ in range(49):
            self.record_latency("bybit", 50.0)
```

또는 duck-typing을 유지하되, 테스트에서 `_MockLatencyTracker` 에 미등록 exchange를 전달하는 케이스 추가.

---

### [MEDIUM] scheduled_tuner.py:93 라인 과도한 길이

**File**: `engine/src/tuning/scheduled_tuner.py:93`

**Issue**: 단일 라인의 list comprehension이 지나치게 길다:

```python
return [s for s, v in data.items() if v is True or (isinstance(v, dict) and v.get("active", False))]
```

**Impact**: 가독성 저하. 조건 로직이 복잡하여 한 줄에서 파악하기 어렵다.

**Fix**: 멀티라인으로 분리:

```python
return [
    s for s, v in data.items()
    if v is True or (isinstance(v, dict) and v.get("active", False))
]
```

---

### [MEDIUM] _apply_shadow_decisions 에서 broad except로 ShadowRunner 오류 무시

**File**: `engine/src/tuning/scheduled_tuner.py:200-201`

**Issue**: `_apply_shadow_decisions` 메서드에서 `except Exception as exc` 가 `ShadowRunner.apply_decision` 의 모든 예외를 경고 로그만 남기고 삼킨다. `ShadowRunner` 가 전략 파라미터를 자동 적용하는 핵심 게이트이므로, 실패 시 해당 전략 결과에 오류 상태가 기록되지 않는다.

```python
except Exception as exc:
    logger.warning("ShadowRunner failed for %s: %s", strategy, exc)
    # data["shadow_decision"] 이 설정되지 않음 -> Telegram 보고에서 "—" 로 표시
```

**Impact**: ShadowRunner 실패가 조용히 무시되어 운영자가 검증 누락을 인지하지 못할 수 있다.

**Fix**: 예외 발생 시 `data["shadow_decision"] = "ERROR"` 를 명시적으로 설정하고, Telegram 알림 레벨을 WARNING으로 올리는 것을 권장:

```python
except Exception as exc:
    logger.warning("ShadowRunner failed for %s: %s", strategy, exc)
    data["shadow_decision"] = "ERROR"
```

---

### [MEDIUM] latency_arb 가 ScheduledTuner.EXCLUDED 에서 누락

**File**: `engine/src/tuning/scheduled_tuner.py:45`

**Issue**: CLAUDE.md 메모리에 따르면 `latency_arb` 는 Shadow에서 비활성 전략 중 하나이다 (Korean stale data 동일 문제). 그러나 `EXCLUDED = {"cex_dex", "statistical_arb"}` 에만 해당 전략이 포함되어 있고, `latency_arb` 는 제외되지 않았다.

프로젝트 메모리:
> **Shadow 비활성 전략**: stat_arb(mean-reversion 비호환), spot_futures(Korean stale data), latency_arb(동일 문제)

`spot_futures` 와 `latency_arb` 도 비활성 상태라면, 이들에 대한 Optuna 최적화 실행이 무의미할 수 있다.

**Impact**: 비활성 전략에 대해 불필요한 Optuna trial을 실행하여 리소스 낭비. 단, `strategy_activation.json` 을 통한 동적 필터링이 가능하므로 심각도는 MEDIUM.

**Fix**: `EXCLUDED` 에 추가하거나, `strategy_activation.json` 에서 관리하는 의도적 설계라면 코드 주석으로 명시:

```python
# latency_arb/spot_futures: Shadow에서 비활성이나, activation.json으로 동적 관리.
# EXCLUDED는 구조적으로 튜닝 불가능한 전략만 포함.
EXCLUDED = {"cex_dex", "statistical_arb"}
```

---

### [LOW] strategy_params.json latency_arb 의 wfe=0.0 은 WFE gate를 통과하지 못함

**File**: `engine/config/strategy_params.json:73`

**Issue**: `latency_arb` 의 `wfe: 0.0` 은 `ScheduledTuner.run_optimization` 의 WFE gate (`best_value > 0`) 를 통과하지 못하므로, 첫 번째 최적화 전까지 `status: "READY"` 가 되지 않는다. 이는 의도적 설계일 수 있으나 (MONITOR 상태에서 시작), 명시적 설명이 없다.

**Impact**: 없음 (의도적 초기값으로 보임). 문서화 권장.

**Fix**: JSON에 `note` 필드 추가:

```json
"latency_arb": {
    "status": "MONITOR",
    "wfe": 0.0,
    "note": "Initial params; wfe=0.0 until first Optuna run completes."
    ...
}
```

---

## 검증 항목 체크리스트

| 항목 | 결과 |
|------|------|
| 이중 슬리피지 금지 위반 | PASS -- PowerLaw 참조 없음 |
| PowerLaw k=0.0 유지 | PASS -- 튜닝 모듈에 PowerLaw 없음 |
| LSP 진단 (4개 소스 파일) | PASS -- 0 errors, 0 warnings |
| 하드코딩된 시크릿 | PASS -- 없음 |
| 빈 catch 블록 | PASS -- 없음 |
| param_bridge 키 일관성 | FAIL -- main.py:579 triangular 불일치 (CRITICAL) |
| SSOT 수식 정합성 | PASS -- min_spread_bps_range 하한 3.0 bps는 프로젝트 최소 MIN_EDGE_BPS=5 와 정합 |
| 테스트 커버리지 | PASS -- 6개 테스트 파일 업데이트, 새 테스트 클래스 5개 추가 |

---

## Recommendation

**REQUEST CHANGES**

CRITICAL 이슈 1건 (`main.py:579` triangular `max_position_usdt` -> `max_position_size_usdt` 키 불일치)과 HIGH 이슈 1건 (`_MockLatencyTracker` 인터페이스 불일치)의 수정이 필요하다.

CRITICAL 이슈는 삼각 전략의 포지션 한도를 10배 증가시키는 실질적 런타임 버그이므로, 반드시 수정 후 재리뷰해야 한다.

### 수정 우선순위

1. **[CRITICAL]** `engine/src/main.py:579` -- `max_position_usdt` -> `max_position_size_usdt` 키 변경
2. **[HIGH]** `engine/src/tuning/strategy_backtest.py:468-482` -- `_MockLatencyTracker` 타입 안전성 개선
3. **[MEDIUM]** `engine/src/tuning/scheduled_tuner.py:200` -- shadow_decision ERROR 상태 추가
4. **[MEDIUM]** `engine/src/tuning/scheduled_tuner.py:93` -- 라인 분리
5. **[MEDIUM]** `engine/src/tuning/scheduled_tuner.py:45` -- EXCLUDED 주석 또는 확장
6. **[LOW]** `engine/config/strategy_params.json:73` -- note 필드 추가
