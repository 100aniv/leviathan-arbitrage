# Plan: Config 단일화 — engine.json을 유일한 설정 소스로

## Context

3곳에서 config을 읽어 20+버그 발생:
1. **engine.json** — 메인 config (218 lines, 올바른 값)
2. **trading.json** — deprecated 표시됐지만 여전히 deep merge로 읽힘 (stale 값)
3. **config.py Pydantic** — env var defaults (stale defaults, e.g. active_exchanges에 bybit/okx 포함)

### 핵심 문제
- `config_loader.py`가 trading.json을 base로 engine.json을 override로 deep merge → trading.json 전용 키가 유령처럼 살아있음
- `main.py _apply_trading_json_defaults()`가 trading.json 값을 env var로 주입 → Pydantic이 이 stale 값을 수령
- `main.py _init_risk()`가 trading.json risk + engine.json risk를 명시적으로 merge
- **BUG-83 근본**: TradingSettings.active_exchanges default = ["binance", "bybit", "okx", "bitget"] — engine.json의 7개와 불일치

### trading.json 고유 키 분석 (engine.json에 없는 것)

| 키 | 값 | 코드 사용 여부 | 판정 |
|---|---|---|---|
| `strategy_filters.funding_ou_min_halflife_s` | 60 | **미사용** (grep 0건) | 삭제 |
| `strategy_filters.min_funding_diff_bps` | 2.0 | 미사용 (코드는 `funding_min_diff_bps` 사용) | 삭제 |
| `slippage.model` | "cex_orderbook" | 미사용 (get_config 호출 0건) | 삭제 |
| `slippage.powerlaw_k` | 0.0 | 미사용 (env var POWERLAW_SLIPPAGE_K 별도) | 삭제 |
| `slippage.gamma` | 0.5 | **사용** (`slippage_model.py:74`) | **engine.json에 추가** |
| `execution.rollback_timeout_ms` | 2000 | Pydantic default=2000 동일 | 안전 (default 일치) |
| `execution.reconciliation_interval_s` | 60 | Pydantic default=5 **불일치** — 하지만 env var 경유 주입 | **engine.json에 추가** |
| `execution.recovery_reconciliation_interval_s` | 60 | 미사용 (grep 0건) | 삭제 |
| `symbol_discovery.min_exchanges` | 3 | Pydantic default=3 동일 | 안전 |
| `shadow` 섹션 전체 | deprecated | 미사용 | 삭제 |

**결론**: engine.json에 추가 필요한 키는 **2개** (`slippage.gamma`, `execution.reconciliation_interval_s`). 나머지는 dead 또는 default 동일.

---

## Work Objectives

engine.json을 유일한 비시크릿 설정 소스로 만들어 config 충돌 근절.

## Guardrails

### Must Have
- engine.json에 누락 키 추가 후 trading.json 제거
- 모든 get_config() 호출이 기존과 동일한 값 반환
- paper/live 모드 동작 변화 0건
- 테스트 전량 통과

### Must NOT Have
- trading.json 값이 engine.json과 다른 상태에서 제거 (값 손실)
- Pydantic Settings 자체 제거 (env var 시크릿 로딩에 필요)
- load_engine_config() 함수 변경 (정상 동작 중)

---

## Task Flow

### Step 1: engine.json에 누락 키 추가

**파일**: `config/engine.json`

변경:
```json
// slippage 섹션에 추가:
"slippage": {
    "k_default": 1.0,
    "conservative_multiplier": 1.5,
    "gamma": 0.5,              // ← 추가 (from trading.json, slippage_model.py:74에서 사용)
    "gamma_calibrated": false,
    "t0": 60.0
}

// execution 섹션에 추가:
"execution": {
    ...existing...,
    "rollback_timeout_ms": 2000,          // ← 추가 (executor.py에서 Pydantic 경유 사용)
    "reconciliation_interval_s": 60       // ← 추가 (main.py env var 주입 경로)
}
```

**AC**: get_config("slippage.gamma") == 0.5, get_config("execution.rollback_timeout_ms") == 2000

---

### Step 2: config_loader.py에서 trading.json 제거

**파일**: `src/core/config_loader.py`

변경:
- `_TRADING_JSON` 경로 변수 삭제
- `_load()` 함수: trading.json 로딩 블록 삭제, engine.json만 로드
- `TRADING_CONFIG_PATH` env var 지원 삭제

```python
# Before:
def _load() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    # Secondary: trading.json (legacy)
    try:
        merged = json.loads(_TRADING_JSON.read_text())
    ...
    # Primary: engine.json (overrides trading.json)
    try:
        engine_cfg = json.loads(_ENGINE_JSON.read_text())
        merged = _deep_merge(merged, engine_cfg)
    ...

# After:
def _load() -> dict[str, Any]:
    try:
        return json.loads(_ENGINE_JSON.read_text())
    except Exception as exc:
        logger.warning("config_loader.engine_load_failed", ...)
        return {}
```

**AC**: `_load()`에 trading.json 참조 0건. `_deep_merge` 함수는 hot-reload 등에서 아직 유용하므로 유지.

---

### Step 3: main.py에서 load_trading_config 제거

**파일**: `src/main.py`

3-a) `_apply_trading_json_defaults()` 메서드 전체 삭제 (line 379~421)

3-b) `_init_config()` (line 423~455):
```python
# Before:
_tcfg = load_trading_config()
if _tcfg:
    self._apply_trading_json_defaults(_tcfg)

# After:
# (삭제 — engine.json이 config_loader/get_config 경유로 직접 제공)
```

3-c) `_init_risk()` (line 1339~1345):
```python
# Before:
_risk_cfg_base = (load_trading_config() or {}).get("risk", {})
_risk_cfg_override = _lec_risk().get("risk", {})
_risk_cfg = {**_risk_cfg_base, **_risk_cfg_override}

# After:
_risk_cfg = _lec_risk().get("risk", {})
```

3-d) import 정리: `load_trading_config` import 제거 (line 34)

**AC**: main.py에서 `load_trading_config` / `_apply_trading_json_defaults` 참조 0건.

---

### Step 4: 나머지 호출자 수정

**4-a) `src/api/routes/settings.py`** (line 43~48):
```python
# Before:
from src.core.config import load_trading_config
tcfg = load_trading_config()
strategy_reqs = tcfg.get("strategy_exchange_requirements", {})

# After:
from src.core.config_loader import get_config
strategy_reqs = get_config("strategy_exchange_requirements", default={})
```
참고: `strategy_exchange_requirements` 키는 현재 trading.json에도 engine.json에도 없음 → 항상 `{}` 반환. 동작 동일.

**4-b) `scripts/run_kbt_backtests.py`** (line 68, 108~118):
```python
# Before: trading.json에 stat_arb_z_threshold 쓰기
_TRADING_JSON_PATH = _ENGINE_ROOT / "config" / "trading.json"
trading = json.loads(_TRADING_JSON_PATH.read_text())
trading.setdefault("strategy_filters", {})["stat_arb_z_threshold"] = new_z
_TRADING_JSON_PATH.write_text(json.dumps(trading, indent=2))

# After: engine.json에 쓰기
_ENGINE_JSON_PATH = _ENGINE_ROOT / "config" / "engine.json"
engine = json.loads(_ENGINE_JSON_PATH.read_text())
engine.setdefault("strategy_filters", {})["stat_arb_z_threshold"] = new_z
_ENGINE_JSON_PATH.write_text(json.dumps(engine, indent=2))
```

**AC**: 프로젝트 전체에서 `load_trading_config` 호출 0건 (정의 제외).

---

### Step 5: Pydantic TradingSettings.active_exchanges default 수정 + trading.json 아카이브

**5-a) `src/core/config.py`**:

`TradingSettings.active_exchanges` default를 engine.json의 실제 값과 일치시키거나, main.py에서 engine.json → env var 주입:
```python
# Option A (간단 — default 일치):
active_exchanges: list[str] = Field(
    default=["binance", "bitget", "upbit", "coinone", "bithumb", "binance_futures", "bitget_futures"],
    description="Exchange IDs to connect to",
)

# Option B (더 나은 — main.py에서 engine.json 값 주입):
# _init_config()에서:
from src.core.config import load_engine_config
_ecfg = load_engine_config()
_active = _ecfg.get("exchanges", {}).get("active", [])
if _active and "TRADING_ACTIVE_EXCHANGES" not in os.environ:
    os.environ["TRADING_ACTIVE_EXCHANGES"] = json.dumps(_active)
```

**추천**: Option B — engine.json이 유일한 소스, Pydantic default는 fallback only.

**5-b) `load_trading_config()` deprecation**:
```python
def load_trading_config() -> dict:
    """DEPRECATED: Use config_loader.get_config() instead. Returns empty dict."""
    import warnings
    warnings.warn("load_trading_config() is deprecated. Use get_config().", DeprecationWarning, stacklevel=2)
    return {}
```

**5-c) `config/trading.json` → `config/trading.json.archived`** (git mv)

**AC**: `trading.json` 파일 없음. Pydantic active_exchanges가 engine.json 값과 일치. BUG-83 근본 수정.

---

## Impact Analysis

| 영역 | 영향 | 설명 |
|------|------|------|
| paper mode | **없음** | engine.json이 이미 primary |
| live mode | **없음** | engine.json이 이미 primary |
| backtest mode | **없음** | 동일 |
| KBT script | **minor** | engine.json에 쓰도록 경로 변경 |
| API /settings | **없음** | strategy_exchange_requirements는 원래 항상 {} |
| 테스트 | **없음** | mock_settings.trading 사용 (Pydantic mock, JSON 무관) |

## Test Strategy

1. **단위 테스트**: `cd engine && python -m pytest tests/ -x --tb=short` — 전량 통과
2. **get_config 검증**: 변경 후 주요 키 값 확인 스크립트
   ```python
   from src.core.config_loader import get_config
   assert get_config("slippage.gamma") == 0.5
   assert get_config("strategy_filters.funding_zscore_threshold") == -1
   assert get_config("risk.max_position_pct") == 6.0
   assert get_config("exchanges.active") == ["binance", "bitget", ...]
   ```
3. **Paper 10분 실행**: config 관련 WARNING/ERROR 0건 확인

## Files Summary

| 파일 | 변경 유형 |
|------|----------|
| `config/engine.json` | 키 2개 추가 (slippage.gamma, execution.reconciliation_interval_s) |
| `src/core/config_loader.py` | trading.json 로딩 제거 |
| `src/main.py` | `_apply_trading_json_defaults` 삭제, `load_trading_config` 호출 2건 제거, engine.json→env var 주입 추가 |
| `src/api/routes/settings.py` | `load_trading_config` → `get_config` 전환 |
| `scripts/run_kbt_backtests.py` | trading.json → engine.json 쓰기 전환 |
| `src/core/config.py` | `load_trading_config` deprecated 처리, TradingSettings default 검토 |
| `config/trading.json` | → `config/trading.json.archived` 아카이브 |
