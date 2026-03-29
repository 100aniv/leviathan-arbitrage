# TF QF Stage 3: Engine Integrity Verification (Post S15-S21)

**Date**: 2026-03-22
**Verifier**: Jeongyeon (deep-executor/opus)
**Scope**: S15~S21 changes — init chain, strategy registration, RiskGuardian, KillSwitch, dead wiring, Assembly 4-check
**Test Results**: 5,242 passed, 0 failed, 12 skipped (316s)

---

## Result: PASS

| Category | Result | Details |
|----------|--------|---------|
| CRITICAL | 0 | No critical issues found |
| HIGH | 0 | No high-severity issues found |
| MEDIUM | 1 | LiveGate continuous monitor timing (by design) |
| LOW | 0 | None |

---

## 1. Initialization Chain Verification

Engine.run() calls 11 init steps sequentially (main.py:159-171):

| # | Method | Line | Result | Notes |
|---|--------|------|--------|-------|
| 1 | `_init_config()` | 359 | OK | Settings loaded, symbols resolved, trading.json defaults applied |
| 2 | `_init_infrastructure()` | 440 | OK | EventBus + DB + Telegram + Rust bridge |
| 3 | `_init_exchanges()` | 619 | OK | Paper/Sandbox/Live mode routing |
| 4 | `_init_signal_pipeline()` | 725 | OK | PriceHub + CostCalculator + SignalGenerator + RegimeDetector + ONNX + MLCanary + AdaptiveThreshold + SlippageFeedbackCollector |
| 5 | `_init_strategies()` | 873 | OK | StrategyManager + 7 strategies registered |
| 6 | `_init_risk()` | 1058 | OK | CircuitBreaker + RiskGuardian + PerStrategyCB + CorrelationMonitor + PortfolioRisk + DQM + ExposureTracker |
| 7 | `_init_execution()` | 1142 | OK | AtomicExecutor + TradeConsumer + SlippageFeedback + DynamicSizer + TCA + Rebalancer + PositionRecovery + Reconciler |
| 8 | `_populate_context()` | 1564 | OK | EngineContext wired with all subsystems |
| 9 | `_startup_position_scan()` | 2647 | OK | US-250 orphan position WAL scan |
| 10 | `_startup_compliance_audit()` | 2681 | OK | US-250-a ComplianceChecker startup audit |
| 11 | `_start_background_tasks()` | 1596 | OK | 10+ background tasks started |
| 12 | `_init_tuner()` | 577 | OK | ScheduledTuner (optional, ENABLE_INLINE_TUNER) |

### S21 Specific Changes Verified:

- **_init_telegram() (line 545)**: TradeTelegramBot direct initialization confirmed. Legacy TelegramAlerter/SmartTelegramAlerter/TelegramCommandHandler removed. Backward-compat `self._telegram = self._trade_bot` correctly wired. TradeTelegramBot has all required methods: `send_alert`, `send_signal_found`, `send_circuit_breaker_event`, `poll_loop`, `schedule_daily_report`, `close`.
- **ShadowMode constructor (shadow.py:382-513)**: Accepts `strategy_filter` (US-299) and `portfolio_risk` (US-300) parameters. Both stored as instance attributes and used in signal processing paths.

---

## 2. Strategy Registration Verification

`_register_default_strategies()` (main.py:917-1019):

| Strategy | Config Source | Registration Condition | Status (strategy_params.json) | Verdict |
|----------|-------------|----------------------|-------------------------------|---------|
| cross_exchange_v1 | CrossExchangeConfig | status in (READY, MONITOR) | READY | REGISTERED |
| spot_futures_v1 | SpotFuturesConfig | status in (READY, MONITOR) | READY | REGISTERED |
| futures_futures_v1 | FuturesFuturesConfig | status in (READY, MONITOR) | MONITOR | REGISTERED |
| triangular_v1 | TriangularConfig | status in (READY, MONITOR) | MONITOR | REGISTERED |
| funding_rate_v1 | FundingRateConfig | status in (READY, MONITOR) | READY | REGISTERED |
| statistical_arb_v1 | StatisticalArbStrategy | status in (READY, MONITOR) | **DISABLED** | **NOT REGISTERED** (US-297 correct) |
| cex_dex_v1 | CexDexStrategy | DEX_RPC_URL set | MONITOR | CONDITIONAL (only with DEX adapter) |

**Result**: 6 strategies registered (stat_arb DISABLED per US-297). CexDex conditional on DEX_RPC_URL. All correct.

---

## 3. RiskGuardian 11-Check Verification

guardian.py implements checks #0 through #12 (13 total checks):

| Check # | Name | Line | Connected | Notes |
|---------|------|------|-----------|-------|
| #0 | Halt check (threading.Event) | 154 | YES | `is_halted()` — CANNOT be bypassed |
| #1 | Position limit | 170 | YES | max_position_pct default 10% |
| #2 | Drawdown limit | 187 | YES | max_drawdown_pct default 2% |
| #3 | Exposure limit | 200 | YES | max_exposure_pct default 30% |
| #4 | Circuit breaker state | 216 | YES | CircuitBreaker.allows_trading() |
| #4e | Net exposure per asset (Amend 7) | 227 | YES | ExposureTracker integration |
| #5 | Exchange health score | 247 | YES | **US-286: DQM preferred** (data_quality_manager.get_health_score) with fallback to portfolio scores |
| #6 | Max single trade size | 268 | YES | max_single_trade_pct default 5% |
| #7 | Volatility check | 284 | YES | 1min/24h ratio check (skip if no data) |
| #8 | Max rollback cost (Amend 3C) | 303 | YES | 3x worst-case slippage + round-trip fees |
| #9 | Strategy correlation (US-118/264) | 325 | YES | CorrelationMonitor + DynamicSizer enforcement |
| #10 | Max concurrent positions (US-154) | 350 | YES | env var MAX_CONCURRENT_POSITIONS (bounds: 1-1000) |
| #11 | Per-strategy capital allocation (US-196) | 364 | YES | capital_allocation_pct dict |
| #12 | Per-strategy circuit breaker (US-222/228) | 387 | YES | PerStrategyCB.is_allowed() + global CB trigger |
| Advisory | Portfolio MDD (US-278) | 411 | YES | **Non-blocking log-only** — PortfolioRiskManager.check_mdd_breach() |

**Wiring in _init_risk() (main.py:1058-1136)**:
- `self._risk_guardian.per_strategy_cb = self._per_strategy_cb` (line 1094) -- CONNECTED
- `self._risk_guardian.correlation_monitor = self._correlation_monitor` (line 1104) -- CONNECTED
- `self._risk_guardian.portfolio_risk = self._portfolio_risk` (line 1111) -- CONNECTED
- `self._risk_guardian.data_quality_manager = self._data_quality_manager` (line 1121) -- CONNECTED

**Result**: All 13 checks connected. US-278 MDD advisory check non-blocking (correct). US-286 DQM integrated into Check #5.

---

## 4. KillSwitch 3-Tier Verification

kill_switch.py implements the full 3-tier chain:

| Tier | Target Latency | Implementation | Connected |
|------|---------------|----------------|-----------|
| Tier 1: Local Halt | < 1ms | `halt_local()` — threading.Event.set() + Redis SET + Rust AtomicBool | YES |
| Tier 2: Cancel Orders | < 500ms | `asyncio.gather` cancel_all_orders on all exchanges (2s timeout per exchange, 1 retry) | YES |
| Tier 3: Close Positions | < 2000ms | `asyncio.gather` close_all_positions on all exchanges (3s timeout, configurable) | YES |

- `is_halted()` checks both Python threading.Event AND Rust AtomicBool (OR logic) -- CORRECT
- `halt_local()` sets KILL_SWITCH_ACTIVE Prometheus metric -- CORRECT
- `clear_halt()` clears both Python and Rust flags -- CORRECT
- `KillSwitchTarget` protocol separate from `ExchangeAdapter` -- CORRECT (protocol conflict resolved)
- `RiskGuardian.emergency_pause()` calls `halt_local()` -- CONNECTED (line 442)
- LiveGate continuous monitor triggers `emergency_pause` after N consecutive failures -- CONNECTED (live_gate.py:356)

**Result**: 3-tier chain fully connected: halt -> cancel -> liquidate.

---

## 5. Dead Wiring Verification (S15-S21 Components)

### PortfolioRiskManager (US-277/278/300)
- **Defined**: `src/core/portfolio_risk.py:23` -- PortfolioRiskManager class
- **Initialized**: `main.py:537` -- `_init_database()` creates `PortfolioRiskManager()`
- **Wired to RiskGuardian**: `main.py:1111` -- `self._risk_guardian.portfolio_risk = self._portfolio_risk`
- **Wired to ShadowMode**: `main.py:2361` -- `portfolio_risk=self._portfolio_risk`
- **Used in shadow.py**: lines 1525-1529 (update_returns), 2236-2248 (get_var/get_portfolio_volatility/check_mdd_breach)
- **Used in guardian.py**: lines 411-428 (advisory MDD check)
- **Verdict**: FULLY CONNECTED (3 call sites: RiskGuardian advisory, ShadowMode returns update, ShadowMode snapshot)

### DataQualityManager (S19, US-286)
- **Defined**: `src/core/data_quality_manager.py:151` -- DataQualityManager class
- **Initialized**: `main.py:1115` -- `_init_risk()` creates `DataQualityManager()`
- **Exchanges registered**: `main.py:1118-1119` -- loop registers all known exchanges
- **Wired to RiskGuardian**: `main.py:1121` -- `self._risk_guardian.data_quality_manager = self._data_quality_manager`
- **Wired to ShadowMode**: `main.py:2359` -- `data_quality_manager=self._data_quality_manager`
- **Used in guardian.py**: lines 248-250 (Check #5: prefer DQM health score)
- **Used in shadow.py**: lines 905, 912 (quality check on orderbook updates)
- **Verdict**: FULLY CONNECTED (RiskGuardian Check #5 + ShadowMode quality checks)

### strategy_filter (US-299)
- **Parameter**: `main.py:2338-2342` -- parsed from `SHADOW_STRATEGY_FILTER` env var
- **Passed to ShadowMode**: `main.py:2360` -- `strategy_filter=_shadow_strategy_filter`
- **Stored in ShadowMode**: `shadow.py:441-442` -- `frozenset(strategy_filter)`
- **Used in signal processing**: `shadow.py:1337-1339` (cross-exchange signals), `shadow.py:1656-1658` (multi-strategy signals)
- **Also in progressive/validation loops**: `main.py:2465, 2546` -- both paths pass strategy_filter
- **Verdict**: FULLY CONNECTED (3 ShadowMode creation paths all pass it, 2 signal processing paths enforce it)

### InsufficientDataError (US-298)
- **Defined**: `src/tuning/scheduled_tuner.py:50` -- custom RuntimeError subclass
- **Raised in**: `scheduled_tuner.py:249, 268` -- pre-flight data validation
- **Caught in**: `scheduled_tuner.py:152` -- graceful handling during tuning cycle
- **Verdict**: FULLY CONNECTED (defined, raised, caught within ScheduledTuner pipeline)

---

## 6. Assembly 4-Check Summary

| Check | Result | Evidence |
|-------|--------|----------|
| Init chain non-None | PASS | All 12 init methods produce non-None subsystems. Each has try/except with warning fallback. |
| Signal flow | PASS | SignalGenerator -> EventBus -> StrategyManager -> TradeRequestConsumer -> AtomicExecutor. Shadow mode uses direct routing (ShadowMode.route_signal). |
| Dead wiring | PASS | All S15-S21 components (PortfolioRiskManager, DataQualityManager, strategy_filter, InsufficientDataError) fully connected with multiple call sites. |
| Config flags | PASS | strategy_params.json controls registration; env vars control features (SHADOW_STRATEGY_FILTER, PORTFOLIO_RISK_ENABLED, CAPITAL_ALLOCATOR_ENABLED, LIVE_GATE_CONTINUOUS_ENABLED). |

---

## 7. Additional Observations (Non-Blocking)

### MEDIUM: LiveGate Continuous Monitor Timing
- `_start_background_tasks()` (line 1711) checks `self._live_gate is not None`, but in shadow mode `_live_gate` is only initialized inside `_shadow_mode_loop()` (line 2389) which runs as a background task created later.
- **Impact**: LiveGate continuous monitor at line 1711 is never created in shadow mode.
- **Mitigation**: In shadow mode, LiveGate is started via `start_auto_evaluation()` (line 2397) inside the shadow loop. The continuous monitor check at 1711 is for non-shadow modes (REAL_AUTHENTICATED).
- **Severity**: MEDIUM -- by design, not a bug. Shadow mode has its own LiveGate lifecycle.

### Telegram 3-Bot Architecture (S20-C/S21)
- TradeTelegramBot: initialized in `_init_telegram()`, backward-compat via `self._telegram = self._trade_bot`
- InfraBot/DevBot: external bot-gateway process (not engine-internal)
- Legacy removed: TelegramAlerter, SmartTelegramAlerter, TelegramCommandHandler
- `_enabled` attribute available on TelegramBotBase + `enabled` property -- both access patterns work

---

## Final Verdict

**PASS** -- 0 CRITICAL, 0 HIGH, 1 MEDIUM (by-design)

All S15-S21 components are properly initialized, wired, and connected. The engine's init chain produces non-None results for all subsystems, signal flow is intact, and no dead wiring exists among the new components. Tests confirm: 5,242 passed, 0 failed, 12 skipped.
