# Analyst Review: Phase K Requirements — ningning-phase-k

Generated: 2026-04-02
Analyst: ningning (oh-my-claudecode:analyst)

---

## 1. Evidence Base

Files examined:
- `/Users/100aniv/Development/arbitrage_OMC/.omc/prd.json` — US-055/056/332/334/358–364 full AC text
- `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py` — `_execute_trade_request` body (lines 660–823)
- `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/db/market_recorder.py` — `record_execution` signature
- `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/db/migrations/001_init_schema.sql` — `orderbook_snapshots` schema
- `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/db/migrations/002_add_mode_column.sql` — `mode` column on `execution_log`
- `/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py` — `ExchangeSettings` existing fields

---

## 2. Confirmed Facts (no assumptions)

### US-358: record_execution() missing from live.py
- **CONFIRMED BUG**: `grep record_execution engine/src/modes/live.py` → 0 matches.
  `_execute_trade_request` (lines 752–823) records to `self._stats.trade_history` (in-memory deque) and publishes to Redis, but never calls `self._market_recorder.record_execution(...)`.
- `execution_log.mode` column EXISTS (migration 002). `record_execution(mode=...)` parameter EXISTS in `market_recorder.py` (line 195). The call site is the only missing piece.
- shadow.py has two call sites (lines 1603 and 1980) that serve as the reference pattern.
- `self._market_recorder` object: must verify it is injected into LiveMode `__init__`. The grep did not confirm this — see open questions.

### US-359: config.py API key fields
- `ExchangeSettings` currently has: Binance (key/secret/testnet), OKX (key/secret/passphrase), Bybit (key/secret/testnet).
- Missing from `ExchangeSettings`: Bitget 4 fields, Upbit 2 fields, Bithumb 2 fields, Coinone 2 fields, Tier4 10 fields = **18 fields total as stated**.
- Note: `bithumb_*` operational fields (refresh_interval, deviation_pct, etc.) already exist in the broader settings class but the **trading credential** fields (api_key/secret) do not.

### US-360: Tier4 adapters
- `_NATIVE_ADAPTER_MAP` in `engine/src/infra/exchange/__init__.py` — not examined in detail; referenced in AC but not verified to be empty for Tier4. This is a risk.
- AC states "NotImplementedError graceful fallback" but does not specify whether the engine skips that exchange or raises to PaperExecutor. The fallback contract is ambiguous.

### US-361: Backtest API
- `BacktestMode.__init__` current signature not examined. AC requires `strategy_ids/seed_capital` params added — this is a breaking change to an existing class constructor. Migration risk for existing unit tests.

### US-362: OHLCV synthetic orderbook
- `orderbook_snapshots` schema (001_init_schema.sql) has NO `source` column.
  Current columns: ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price.
  US-362 AC requires inserting with `source='ohlcv_synthetic'` to distinguish synthetic from real data — **this requires a new migration (006)** that is not listed in any US AC.
- `MarketRecorder.record_orderbook()` does not accept a `source` parameter (signature confirmed, lines 135–176). Adding `source` requires changing both the migration AND the recorder method AND the INSERT SQL template.

### US-364: iMessage gate
- `imessage_gate.py` does not exist anywhere in the codebase (grep for `imessage` returned 0 matches).
- AppleScript is macOS-only. No AC specifies what happens in a Docker/Linux deployment context.
- The "응답 없음 10분 → 재전송 1회 → SKIP" policy is defined in AC but the retry timer implementation is unspecified (asyncio.sleep? celery? background task?).

### US-332: SF 24H Progressive Shadow
- AC lists only 3 criteria: LiveGate 6-check PASS, Sharpe>=2.0, TCA 데이터 수집.
- No AC specifies which strategies must produce trades>=1 during the 24H run.
- No AC specifies what "TCA 데이터 수집" means measurably (row count threshold? specific table?).

### US-334: Sandbox Testnet
- AC lists only 2 criteria. No AC specifies which exchange's sandbox/testnet is used, what the test order size/symbol is, or what "Canary Alpha $70/거래소" means in terms of a pass/fail check.

### US-055: LiveGate Preflight 10항목
- AC lists 3 criteria. The "10항목" are not enumerated in the AC text itself.
- "API 키 설정 완료 (Binance, Upbit, Bithumb)" — Upbit and Bithumb API key fields don't exist in config yet (US-359 prerequisite), making US-055 unachievable without US-359 completing first. **Missing dependency: US-055 should declare US-359 as a dependency.**

### US-056: Live 모드 전환
- "소액 실 거래 1건 이상 성공" — no definition of "소액" (small amount). No floor/ceiling specified. No specification of which exchange executes the first trade.

---

## 3. Missing Acceptance Criteria (by US)

### US-358
- No AC specifies what value `mode` should be when live.py runs in `paper` sub-mode (execution_mode='paper' via LiveMode). Should it be `mode='paper'` or `mode='live'`? The AC only mentions `mode='live'`.
- No AC covers the `_execute_direct_signal` fallback path — it also calls `_execute_trade_request` so the fix covers it, but the AC does not mention this second call site explicitly.
- Missing: verification that `self._market_recorder` is non-None before calling (null guard pattern consistent with shadow.py:1975 `if self._market_recorder is not None`).

### US-359
- No AC specifies env var naming convention for Tier4 fields (e.g. `MEXC_API_KEY` vs `MEXC_API_SECRET` — case, underscores, prefix pattern must be consistent with existing fields).
- No AC specifies what `engine.py` does for Upbit/Bithumb/Coinone conditional adapter creation (only Bitget is mentioned in AC item 6).

### US-360
- No AC specifies the WS collector wiring — adapters for trading exist but are the Tier4 exchanges also added to the collector registry for orderbook data? Or trading only?
- No AC defines which REST base URLs to use for each Tier4 exchange (sandbox vs production).
- "graceful fallback" is undefined: does the adapter raise `NotImplementedError`, return `None`, or switch to `PaperExecutor`? The AC mentions `NotImplementedError` in the WIRING item but the error handling contract is unspecified.

### US-361
- No AC specifies what happens if `start_date/end_date` range has no data in `orderbook_snapshots` — should it fail with 404, trigger a download, or return empty results?
- No AC specifies whether concurrent backtest runs are allowed or if a lock prevents two simultaneous runs.
- `by_exchange` field in `BacktestResult` — no definition of its structure (dict of exchange→PnL? dict of exchange→trade_count?).

### US-362
- **CRITICAL GAP**: `orderbook_snapshots` table has no `source` column. A migration adding `ALTER TABLE orderbook_snapshots ADD COLUMN source TEXT DEFAULT 'live'` is required but no US owns this migration.
- `MarketRecorder.record_orderbook()` has no `source` parameter. Adding it is a breaking change to the method signature used by all live/shadow/paper modes.
- No AC specifies whether synthetic orderbook rows use the same `bids_json`/`asks_json` structure (single-level synthetic book with bid/ask only, vs multi-level real book). The difference matters for strategy evaluation during backtest.
- No AC specifies rate limiting for the Binance `/api/v3/klines` API (1200 requests/minute weight limit).

### US-363
- No AC specifies whether POST /api/paper/start can run concurrently with an existing paper session or must stop it first.
- No AC specifies the `duration_hours` maximum value or what happens when duration expires (auto-stop vs run indefinitely).
- GET /api/paper/result — no AC specifies whether it returns the current in-progress stats or only final stats after session ends.

### US-364
- No AC specifies the macOS-only / Linux-Docker deployment constraint for AppleScript. If the engine runs in Docker on Linux, `osascript` is unavailable.
- No AC specifies what happens if both iMessage AND Telegram fallback are unavailable (engine blocked indefinitely vs auto-SKIP).
- "G-B/G-P/G-L" gate stages are named in the title but not defined in AC — what triggers G-B (backtest gate), G-P (paper gate), G-L (live gate)?

### US-332
- No AC specifies minimum trades per strategy during the 24H run (triangular must produce N trades).
- No AC specifies what "TCA 데이터 수집" means measurably.
- No AC specifies the execution environment (local vs Docker) or which config file governs the 24H run.

### US-334
- No AC specifies which exchange's testnet is used (Binance Testnet? Bybit Testnet?).
- No AC specifies the test order: symbol, size, type (market/limit).
- "Canary Alpha $70/거래소 기준 수립" — no pass/fail criterion. What does "수립" (established) mean as a testable outcome?

### US-055
- The 10 preflight items are not enumerated. "10항목 PASS" is not a testable criterion without knowing what the 10 items are.
- Missing dependency on US-359 (Upbit/Bithumb API key fields must exist before preflight can check them).

### US-056
- "소액" (small amount) is undefined. No floor/ceiling.
- No AC specifies which exchange executes the first live trade.
- No AC specifies the rollback procedure if the first live trade fails mid-leg.

---

## 4. Scope Risks

1. **US-362 schema migration scope creep**: Adding `source` column to `orderbook_snapshots` also requires updating `MarketRecorder.record_orderbook()`, all callers (live.py, shadow.py, backtest.py), and the INSERT SQL. This is a multi-file change not scoped in US-362.

2. **US-360 WS collector scope**: The 5 Tier4 adapters are trading adapters only. If any future US requires orderbook data from these exchanges, the collector wiring is absent. Phase K may need to clarify whether Tier4 is trading-only.

3. **US-364 platform dependency**: iMessage/AppleScript is macOS-only. If the engine is deployed in Docker on Linux for Phase K testing, US-364 cannot be tested. The Telegram fallback must be the primary test path in CI.

4. **US-361/US-362 dependency on existing backtest data**: If `orderbook_snapshots` is empty (no prior Shadow run), US-362 is the only source of backtest data. But US-362 depends on US-361 completing first. If US-361 has bugs, US-362 is blocked.

5. **US-359 → US-360 KRW exchange activation risk**: Adding Upbit/Bithumb/Coinone API keys to config may activate those exchanges in the `active_exchanges` list if engine.py initialization logic is not carefully conditioned. This could cause unexpected KRW pair signals during Phase K testing.

---

## 5. Unvalidated Assumptions

1. **US-358 assumes `self._market_recorder` is injected into LiveMode**: Not confirmed from grep. Must verify `LiveMode.__init__` accepts and stores a `MarketRecorder` instance. If it does not, US-358 requires injecting the dependency first.

2. **US-362 assumes `orderbook_snapshots` has a `source` column**: It does not. The assumption embedded in the AC is false. A migration must be written and applied before any synthetic data INSERT.

3. **US-360 assumes NativeAdapterBase interface is stable**: If the base class changes in parallel (e.g. from another US), all 5 adapters break. Validate that NativeAdapterBase is frozen for Phase K.

4. **US-332 assumes Shadow 13항목 metrics still apply unchanged**: Since Phase J added WFA and ML A/B features, the 13항목 may need updating. The AC does not reference the current 13항목 list.

5. **US-056 assumes US-334 testnet validation is sufficient proof-of-live**: Testnet fills are simulated. Real exchange behavior (partial fills, order rejection, KYC limits) is not covered.

---

## 6. Dependency Chain Analysis

### Stated chain (from prd.json):
```
US-358 → US-359 → US-360          (exchange infrastructure)
US-358 → US-361 → US-362          (backtest data pipeline)
US-361 → US-363 → US-364          (paper/live API + gate)
```

### Gap: US-055 missing dependency
US-055 requires "API 키 설정 완료 (Upbit, Bithumb)" but declares no dependencies. US-359 must complete before US-055 can pass. The dependency `US-055 → depends on → US-359` is missing from prd.json.

### Gap: US-332 missing dependency on US-358
US-332 (24H Shadow) tests execution_log data quality. If US-358 is not complete, execution records are missing from the DB, making the TCA data collection criterion impossible. US-332 should declare US-358 as a dependency.

### Gap: US-056 missing dependency on US-364
US-056 (Live 모드 전환) requires user approval before live trading. US-364 implements the approval gate. US-056 should declare US-364 as a dependency.

### Recommended execution order (critical path):
```
US-358 (CRITICAL, no deps)
  ↓
US-359 (config fields)
  ↓
US-360 (Tier4 adapters)     ← parallel with US-361/362/363
  ↓
US-361 (backtest API)
  ↓
US-362 (OHLCV downloader)   ← requires migration 006 for source column
  ↓
US-332 (24H Shadow)         ← needs US-358 complete
US-363 (paper API)
  ↓
US-364 (iMessage gate)
  ↓
US-055 (LiveGate preflight) ← needs US-359 + US-364 complete
  ↓
US-334 (sandbox testnet)
  ↓
US-056 (first live trade)   ← needs US-055 + US-334 + US-364 complete
```

---

## 7. Edge Cases

### US-358
- EC-1: `self._market_recorder` is None (not injected) → must guard with `if self._market_recorder is not None` before calling, consistent with shadow.py:1975 pattern.
- EC-2: LiveMode in `paper` sub-mode (`execution_mode='paper'`) — should `mode` parameter be `'paper'` or `'live'`? Current AC only mentions `mode='live'`.
- EC-3: Multi-leg trade (3+ legs, e.g. triangular) — the AC references 2-leg shadow.py:1603 pattern but triangular has 3 legs. Which buy/sell exchange to record?

### US-359
- EC-1: Both Bitget and NativeBitget already exist in some form — adding config fields may create duplicate initialization if engine.py already creates a Bitget adapter unconditionally.
- EC-2: `coinone_access_token` pattern differs from all other `*_api_key` naming — potential env var inconsistency.

### US-360
- EC-1: API key present but invalid (expired/revoked) → adapter initialized but first order fails. The AC does not specify error handling after successful initialization.
- EC-2: OrangeX is a perpetuals-only exchange — `get_balance` for spot may be undefined. AC does not distinguish spot vs futures method contracts.

### US-362
- EC-1: `source` column does not exist in `orderbook_snapshots` → INSERT fails with column not found. This blocks US-362 entirely until migration 006 is applied.
- EC-2: Binance API returns empty data for requested date range (e.g. symbol delisted) → `download_and_store` returns 0 rows. BacktestMode must handle 0-row data gracefully.
- EC-3: Triangular strategy requires 3 simultaneous symbol pairs (BTC/USDT, ETH/USDT, ETH/BTC). If only BTC/USDT and ETH/USDT are downloaded, triangular will find no opportunities. AC does not require ETH/BTC download.

### US-364
- EC-1: Engine running in Docker on Linux → `osascript` not found → AppleScript call raises `FileNotFoundError`. Telegram fallback must activate automatically.
- EC-2: Phone number `+821071763388` hardcoded in AC — if number changes, requires code change rather than config change. Should be config-driven.
- EC-3: User responds with partial text (e.g. "응응" instead of "응") → AC does not define exact match string for approval.

### US-332
- EC-1: Sharpe>=2.0 threshold — if triangular is the only active strategy and its Sharpe is 1.8, does the 24H run fail? AC does not specify per-strategy vs portfolio-level Sharpe.

### US-056
- EC-1: First live trade is partially filled (1 leg fills, other leg rejected) → leg_risk event fires. AC does not specify whether a partial fill counts as "1건 이상 성공".

---

## 8. Missing US (Phase K plan vs prd.json)

The following items are implied by the dependency analysis but have no US:

1. **Migration 006**: `ALTER TABLE orderbook_snapshots ADD COLUMN source TEXT DEFAULT 'live'` — required by US-362 but owned by no US. Recommend adding as US-365 or merging into US-362 AC.

2. **MarketRecorder.record_orderbook() source parameter**: Required by US-362 synthetic INSERT. No US owns the method signature change.

3. **LiveMode `__init__` market_recorder injection audit**: If `self._market_recorder` is not currently in LiveMode (not confirmed from grep), US-358 requires a DI change that is not mentioned in its AC.

---

## 9. Open Questions

- [ ] Does `LiveMode.__init__` currently accept and store a `MarketRecorder` instance? If not, US-358 requires DI wiring that is not scoped in its AC. — Blocks US-358 implementation path.
- [ ] For US-358: when LiveMode runs in `paper` sub-mode, should `record_execution` be called with `mode='paper'` or `mode='live'`? — Affects DB query correctness for US-332 TCA collection.
- [ ] For US-362: who owns migration 006 to add `source TEXT` to `orderbook_snapshots`? — Without this migration, US-362 cannot INSERT synthetic rows. Must be resolved before US-362 starts.
- [ ] For US-362 triangular strategy: must ETH/BTC OHLCV also be downloaded? The AC only lists BTC/USDT and ETH/USDT. — If ETH/BTC is absent, triangular finds 0 opportunities in backtest.
- [ ] For US-360: are Tier4 adapters trading-only, or do they also need WS orderbook collectors? — Scope definition affects implementation size significantly.
- [ ] For US-364: is the phone number `+821071763388` config-driven or hardcoded? Should it be an env var (e.g. `IMESSAGE_APPROVAL_PHONE`)? — Hardcoding creates maintenance risk.
- [ ] For US-055: what are the 10 preflight items? They are referenced by count but not enumerated in the AC. — "10항목 PASS" is not testable without this list.
- [ ] For US-056: what is "소액" (small amount) as a concrete USD value? — "소액 실 거래 1건 이상 성공" is not measurable without a floor/ceiling.
- [ ] For US-332: is Sharpe>=2.0 measured at portfolio level or per-strategy? — Triangular-only Sharpe may differ from portfolio Sharpe.
- [ ] For US-334: which exchange testnet (Binance vs Bybit)? What order size/symbol? — "Sandbox API 연결 확인" and "Canary Alpha $70/거래소 기준 수립" are not independently verifiable without these specifics.

---

## 10. Priority Ranking

| Priority | Item | Blocks |
|----------|------|--------|
| CRITICAL | US-358 `record_execution` call missing — confirmed by grep | US-332, US-056 data integrity |
| CRITICAL | US-362 `source` column missing from `orderbook_snapshots` schema | US-362 entire implementation |
| HIGH | US-055 missing dependency on US-359 in prd.json | Phase K execution order |
| HIGH | US-056 missing dependency on US-364 in prd.json | Live gate enforcement |
| HIGH | US-332 missing dependency on US-358 in prd.json | TCA data completeness |
| HIGH | US-055 "10항목" not enumerated | Cannot write tests |
| HIGH | US-364 AppleScript Linux/Docker gap | CI test feasibility |
| MEDIUM | US-360 fallback contract undefined (NotImplementedError vs None vs PaperExecutor) | Error handling consistency |
| MEDIUM | US-361 concurrent backtest run policy undefined | State corruption risk |
| MEDIUM | US-056 "소액" undefined | Pass/fail criterion missing |
| LOW | US-359 env var naming convention for Tier4 unspecified | Developer ambiguity |
| LOW | US-364 phone number hardcoded vs config-driven | Maintenance risk |
