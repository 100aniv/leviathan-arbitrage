# Phase K Entry Gate Analysis — karina-phase-k

> Architect: Phase K Entry Gate + Code Exploration
> Date: 2026-04-02
> Status: **CONDITIONAL PASS** (2 DRIFT items require fix before Stage B)

---

## 1. 3-Way Consistency Check

### check_all: 9/9 OK

```
[OK] 파일_존재        — 3개 소스 모두 존재
[OK] PRD_카운트       — 353/357 통과
[OK] 활성_Phase       — K
[OK] 테스트_수        — 5,379 passed
[OK] SSOT_해시_드리프트 — 일치
[OK] CLAUDE_MD_동기화  — Phase K
[OK] State_4파일_동기화 — Phase K
[OK] Phase_이력_완료표시 — 13개 완료
[OK] TF_Status_일치    — QF round=11 PASS
```

### PRD Counts

| Source | passes:true | passes:false | Total |
|--------|-------------|--------------|-------|
| prd.json (actual) | 353 | 4 | 357 |
| SSOT.md line 4 | 353 | 4 | 357 |
| CLAUDE.md line 295 | 353 | 4 | 357 |

**Result: MATCH**

passes:false US list:
- US-055: LiveGate + Preflight 10항목
- US-056: Live 모드 전환 (사용자 승인)
- US-332: SF 24H Progressive Shadow 재실행
- US-334: 소액 Live 전환 기준 + Sandbox Testnet 검증

### Test Count

| Source | Value |
|--------|-------|
| SSOT.md line 35 | 5,379 |
| CLAUDE.md line 296 | 5,379 |
| check_all | 5,379 |

**Result: MATCH**

### Phase Sequence

| Source | Value |
|--------|-------|
| SSOT.md line 6 (실행 순서) | `...Phase I ✅ → **J** → K → ...` |
| SSOT.md line 34 (Phase) | `K (Phase J 완료)` |
| SSOT.md line 38 (TF Status) | `...Phase J ✅ → **K** → ...` |
| CLAUDE.md line 295 | `...Phase J✅ → **K** → ...` |

**Result: DRIFT** — SSOT.md line 6 shows `**J**` (bold = active) instead of `J ✅`. Lines 34 and 38 correctly show Phase J as completed and K as active. This is a cosmetic but important inconsistency.

**Fix instruction**: `SSOT.md:6` — Change `→ **J** → K →` to `→ J ✅ → **K** →`

### Next Task

| Source | Value |
|--------|-------|
| SSOT.md line 39 | US-055/056 LiveGate + Live전환 (Phase K) + US-332/334 SF 24H Shadow |
| CLAUDE.md line 298 | US-055/056 LiveGate + Live전환 (Phase K) + US-332/334 SF 24H Shadow |

**Result: MATCH**

---

## 2. Code Exploration (Phase K Target Files)

### 2.1 `engine/src/core/config.py` — API Key Fields

**Findings** (file: `/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py`):

| Exchange | API Key Field | Present? |
|----------|--------------|----------|
| Binance | `binance_api_key` (line 213) | YES |
| OKX | `okx_api_key` (line 219) | YES |
| Bybit | `bybit_api_key` (line 226) | YES |
| Bitget | - | **NO** |
| Upbit | - | **NO** |
| Bithumb | - | **NO** |
| Coinone | - | **NO** |

**Analysis**: `ExchangeSettings` (line 207-229) only defines API key fields for Binance, OKX, Bybit. Bitget/Upbit/Bithumb/Coinone have NO API key fields in the Pydantic config. SSOT.md line 48 says "API 키: Binance ✅ Upbit ✅ Bithumb ✅ Coinone ✅" — these keys must exist in `.env` but are loaded via raw `os.getenv()` rather than through the structured config. This is a wiring gap for Phase K Live mode.

### 2.2 `engine/src/infra/exchange/__init__.py` — Native Adapter Map

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/exchange/__init__.py:51-58`

```python
_NATIVE_ADAPTER_MAP = {
    "binance": BinanceNativeAdapter,
    "bybit": NativeBybitAdapter,
    "okx": NativeOKXAdapter,
    "bitget": NativeBitgetAdapter,
    "upbit": NativeUpbitAdapter,
    "bithumb": NativeBithumbAdapter,
}
```

**Missing**: `coinone` is NOT in `_NATIVE_ADAPTER_MAP`. Only a legacy CCXT-based `CoinoneAdapter` exists (line 22). No `native_coinone.py` file exists in the directory.

Native adapter files present (7 files):
- `native_binance.py`, `native_bybit.py`, `native_okx.py`, `native_bitget.py`
- `native_upbit.py`, `native_bithumb.py`
- `native_adapter.py` (base class)

**Missing adapters** (per Phase K plan): MEXC, Gate.io, BingX, LBank, OrangeX — none exist. Coinone native adapter also missing.

### 2.3 `engine/src/modes/live.py` — record_execution() Call

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py` (1,231 lines)

**CRITICAL FINDING**: `record_execution()` is NEVER called in LiveMode.

- Line 190: `market_recorder` parameter is accepted in `__init__`
- Line 210: `self._market_recorder = market_recorder` (stored)
- Line 551-562: `self._market_recorder.record_orderbook()` is called (orderbook recording works)
- Lines 660-822: `_execute_trade_request()` — NO `record_execution()` call after trade completion
- Lines 1149-1168: `_persist_stats()` — only writes aggregate stats to `engine_state` table, not individual executions

**Comparison with other modes**:
- `shadow.py:1603` — calls `self._market_recorder.record_execution(mode="paper")`
- `shadow.py:1980` — calls `record_execution` for multi-leg trades
- `backtest.py:373` — calls `self._market_recorder.record_execution(mode="backtest")`
- `live.py` — **ZERO calls to record_execution**

**Impact**: In Live mode, individual trade executions will NOT be recorded to TimescaleDB `execution_log` table. This means:
1. No persistent trade history for audit/compliance
2. No data for TCA (Transaction Cost Analysis) post-trade
3. No data for WFA (Walk-Forward Analysis) retraining
4. Dashboard trade history will be empty for live trades
5. Daily reconciliation (K-5) impossible without DB records

**Severity**: CRITICAL — must fix before any live trading.

### 2.4 `engine/src/modes/backtest.py` — BacktestResult Fields

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/backtest.py:39-58`

```python
@dataclass
class BacktestResult:
    start_time: str = ""
    end_time: str = ""
    duration_s: float = 0.0
    snapshots_replayed: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    by_strategy: dict[str, dict] = field(default_factory=dict)
    pnl_curve: list[float] = field(default_factory=list)
    error: str = ""
```

**Status**: Complete. 19 fields including per-strategy breakdown and pnl_curve. Sharpe uses `sqrt(8760)` (line 413) per SSOT section 4.5.

### 2.5 `engine/src/api/routes/backtest.py` — POST /api/backtest/start

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/api/routes/backtest.py`

**Endpoints present**:
- `GET /api/backtest/result` (line 20) — returns latest result
- `GET /api/backtest/wfa` (line 49) — returns WFA results

**Missing**: `POST /api/backtest/start` — NO start endpoint exists. Backtest can only be triggered via `EngineMode.BACKTEST` config, not via API. Phase K plan K-3B requires this.

### 2.6 Missing Files

| File | Exists? | Phase K Relevance |
|------|---------|------------------|
| `engine/src/infra/db/ohlcv_downloader.py` | **NO** | K-4 OHLCV 다운로더 |
| `engine/src/infra/imessage_gate.py` | **NO** | K-7 iMessage gate |
| `engine/src/api/routes/paper.py` | **NO** | K-5 Paper API |

### 2.7 `engine/config/engine.json` — capital.tiers.alpha

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/config/engine.json:8-15`

```json
"capital": {
    "tier": "alpha",
    "tiers": {
        "alpha": {"initial_usd": 34},
        "beta": {"initial_usd": 750},
        "production": {"initial_usd": 5000}
    }
}
```

**Status**: Present. Alpha tier = $34 initial capital. Matches SSOT line 41 "max_position=$10, daily_loss=$15".

---

## 3. US-333 prd.json Status

**File**: `/Users/100aniv/Development/arbitrage_OMC/.omc/prd.json:6123-6127`

```json
{
    "id": "US-333",
    "phase": "S26",
    "title": "TCA 기반 min_profitability 재보정",
    "passes": true
}
```

**Finding**: US-333 EXISTS in prd.json but it is a DIFFERENT US ("TCA 기반 min_profitability 재보정", phase S26, passes:true). It is NOT the "DB 모드 분리 배선 수정 (LiveMode record_execution 미호출)" issue described in the task.

**Conclusion**: The LiveMode `record_execution` gap needs a NEW US registration. Suggest: **US-358** (or next available ID) — "LiveMode record_execution DB 배선" with CRITICAL priority.

---

## 4. Phase K SSOT Roadmap vs Code Gap Matrix

Based on SSOT.md lines 618-633 (Phase K section):

| SSOT K-item | Code Status | Gap |
|-------------|------------|-----|
| K-1: US-055 LiveGate 6-check | `live_gate.py` exists, LiveGateSettings in config.py | Ready for testing |
| K-2: US-056 사장님 승인 | Process gate (non-code) | N/A |
| K-3: Binance API Live + rate limiter | Rate limiter exists in live.py:704-713 | Ready |
| K-4: funding_rate + futures_futures Live | LiveMode executor routing exists | record_execution MISSING |
| K-5: 3-way reconciliation | No reconciliation endpoint/logic in live.py | NEW US needed |
| K-6: slippage/fee 실측 vs 예측 | TCA exists (US-333) but needs live data | Blocked by record_execution gap |
| K-7: US-332/334 SF 24H Shadow | Shadow mode functional | Ready for execution |

---

## 5. Findings Summary

### CRITICAL (must fix before Stage B)

| # | Issue | File:Line | Current | Required |
|---|-------|-----------|---------|----------|
| C1 | LiveMode `record_execution` 미호출 | `live.py:775-817` | Trade 결과가 in-memory + Redis만 저장 | `_execute_trade_request()` 성공 후 `self._market_recorder.record_execution(mode="live")` 호출 추가 (shadow.py:1603-1624 패턴 참조) |
| C2 | Coinone native adapter 부재 | `__init__.py:51-58` | `_NATIVE_ADAPTER_MAP`에 coinone 없음. CCXT legacy만 존재 | `native_coinone.py` 생성 + `_NATIVE_ADAPTER_MAP`에 등록 |

### DRIFT (cosmetic but must fix for consistency)

| # | Issue | File:Line | Current | Required |
|---|-------|-----------|---------|----------|
| D1 | SSOT 실행 순서 Phase J 미완료 표시 | `SSOT.md:6` | `→ **J** → K →` | `→ J ✅ → **K** →` |

### GAP (Phase K new US candidates)

| # | New US | Priority | Description |
|---|--------|----------|-------------|
| G1 | US-358: LiveMode record_execution 배선 | P0 CRITICAL | `_execute_trade_request()` 체결 후 `market_recorder.record_execution(mode="live")` 호출. WIRING AC: (1) record_execution 호출 코드 추가 (2) mode="live" 파라미터 전달 (3) Shadow 10min에서 DB execution_log에 live 모드 레코드 존재 확인 |
| G2 | POST /api/backtest/start 엔드포인트 | P2 | 현재 GET /result + GET /wfa만 존재. start 트리거 API 부재 |
| G3 | ExchangeSettings API 키 필드 확장 | P1 | Bitget/Upbit/Bithumb/Coinone API key fields를 Pydantic config에 추가 (현재 os.getenv 직접 사용) |
| G4 | OHLCV Downloader | P2 | `engine/src/infra/db/ohlcv_downloader.py` 미존재 |
| G5 | iMessage Gate | P3 | `engine/src/infra/imessage_gate.py` 미존재 |
| G6 | Paper API routes | P2 | `engine/src/api/routes/paper.py` 미존재 |
| G7 | Coinone native adapter | P1 | `native_coinone.py` 미존재. WIRING AC: (1) NativeCoinoneAdapter 클래스 생성 (2) `_NATIVE_ADAPTER_MAP`에 "coinone" 등록 (3) engine.json active exchanges에서 coinone 사용 시 native adapter 로드 확인 |

---

## 6. Verdict

**CONDITIONAL PASS** — check_all 9/9 OK, PRD/Test/Phase 3-way 일치 확인.

Stage B 진입 전 필수 수정 2건:
1. **D1**: SSOT.md line 6 실행 순서 `**J**` → `J ✅ → **K**` 수정 (ssot-keeper)
2. **G1/C1**: US-358 prd.json 등록 + LiveMode record_execution 배선 (CRITICAL)

Phase K 실행 시 추가 등록 권장 US: G2~G7 (총 6건, P1~P3)
