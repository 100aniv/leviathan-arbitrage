# codex advisor artifact

- Provider: codex
- Exit code: 0
- Created at: 2026-04-17T03:54:12.628Z

## Original task

LEVIATHAN arbitrage engine — Review commits 3cfb65c, 1a5c80a, 80df207, cb0312d for structural refactoring correctness.

Context:
- WS-1 (3cfb65c): Config unification — removed trading.json deep-merge from config_loader.py, added 3 missing Pydantic overrides in config.py (_apply_engine_json_overrides: circuit_breaker_cooldown_seconds, circuit_breaker_api_error_rate_threshold, live_gate.mdd_threshold), replaced 3 direct engine.json file readers with get_config()/load_engine_config()
- WS-2 (3cfb65c): Pipeline decoupling — added 4 separate callbacks to BaseStrategy (handle_entry_rollback, handle_exit_rollback, handle_entry_success, handle_exit_success) + clear_ghost() method. Migrated 5 strategies (FF/FR/SF/StatArb/Triangular). Migrated all 4 on_execution_rollback call sites in live.py + main.py
- WS-3 (1a5c80a): PositionManager wired — asyncio.ensure_future(open_position/close_position) in _on_execution_result + live.py trade success path
- BUG-93 (80df207): LiveMode missing position_manager param — added it and wired at trade success
- BUG-94 (cb0312d): Optimistic write removal in futures_futures.py on_signal — new _pending_position_metadata dict replaces direct _open_positions write. Promotion happens in on_execution_success.

Review focus:
1. Can a ghost position STILL be created through any path?
2. Race conditions between on_signal (optimistic → pending), on_execution_success (pending → open), handle_entry_rollback (clear pending)?
3. What if on_execution_success is never called (e.g., live.py crash during execution)? Memory leak in _pending_position_metadata?
4. Is the new 4-callback semantics (entry vs exit rollback) correctly separated in live.py + main.py call sites?
5. Are Pydantic overrides complete (which ones might still be missing)?
6. Any correctness issue I might have overlooked?

Files to check:
- engine/src/strategies/futures_futures.py (BUG-94 key changes)
- engine/src/strategies/base.py (BaseStrategy 4-callback)
- engine/src/modes/live.py (trade_executed + rollback paths + position_manager wiring)
- engine/src/main.py (_on_execution_result + PositionManager init)
- engine/src/core/config.py (_apply_engine_json_overrides)

Return: CRITICAL/HIGH/MEDIUM/LOW findings with file:line references.

## Final prompt

LEVIATHAN arbitrage engine — Review commits 3cfb65c, 1a5c80a, 80df207, cb0312d for structural refactoring correctness.

Context:
- WS-1 (3cfb65c): Config unification — removed trading.json deep-merge from config_loader.py, added 3 missing Pydantic overrides in config.py (_apply_engine_json_overrides: circuit_breaker_cooldown_seconds, circuit_breaker_api_error_rate_threshold, live_gate.mdd_threshold), replaced 3 direct engine.json file readers with get_config()/load_engine_config()
- WS-2 (3cfb65c): Pipeline decoupling — added 4 separate callbacks to BaseStrategy (handle_entry_rollback, handle_exit_rollback, handle_entry_success, handle_exit_success) + clear_ghost() method. Migrated 5 strategies (FF/FR/SF/StatArb/Triangular). Migrated all 4 on_execution_rollback call sites in live.py + main.py
- WS-3 (1a5c80a): PositionManager wired — asyncio.ensure_future(open_position/close_position) in _on_execution_result + live.py trade success path
- BUG-93 (80df207): LiveMode missing position_manager param — added it and wired at trade success
- BUG-94 (cb0312d): Optimistic write removal in futures_futures.py on_signal — new _pending_position_metadata dict replaces direct _open_positions write. Promotion happens in on_execution_success.

Review focus:
1. Can a ghost position STILL be created through any path?
2. Race conditions between on_signal (optimistic → pending), on_execution_success (pending → open), handle_entry_rollback (clear pending)?
3. What if on_execution_success is never called (e.g., live.py crash during execution)? Memory leak in _pending_position_metadata?
4. Is the new 4-callback semantics (entry vs exit rollback) correctly separated in live.py + main.py call sites?
5. Are Pydantic overrides complete (which ones might still be missing)?
6. Any correctness issue I might have overlooked?

Files to check:
- engine/src/strategies/futures_futures.py (BUG-94 key changes)
- engine/src/strategies/base.py (BaseStrategy 4-callback)
- engine/src/modes/live.py (trade_executed + rollback paths + position_manager wiring)
- engine/src/main.py (_on_execution_result + PositionManager init)
- engine/src/core/config.py (_apply_engine_json_overrides)

Return: CRITICAL/HIGH/MEDIUM/LOW findings with file:line references.

## Raw output

```text
**CRITICAL**
- Exit rollback semantics are still wrong in both pipelines. `live.py`’s pre-exec rollback helper always dispatches `handle_entry_rollback()` regardless of whether the request is an exit, and the post-exec non-success branch does the same for every `ROLLED_BACK`/`REJECTED` result. That breaks the WS-2 contract for reduce-only exits: FF should restore `_pending_exits -> _open_positions`, and FR should restore `_pending_settlement_positions -> _open_positions`, but neither happens on these paths. Result: a real exchange position can remain open while strategy tracking is not restored, so the monitor stops managing it. See [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1908), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1414), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:246), [funding_rate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/funding_rate.py:471). The same bug still exists in the `TradeRequestConsumer` callback path wired via [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:1566) and handled at [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:2093).

- `BUG-94` removes optimistic `_open_positions`, but `_pending_position_metadata` is not part of the per-symbol duplicate guard and has no expiry/reconciliation path. If execution succeeds on exchange but `on_execution_success()` is never reached, that dict entry leaks indefinitely, and the same symbol can still re-enter because `on_signal()` only blocks `_pending_entry_symbols` and `_open_positions`. The global slot count may still allow it. That is more than a memory leak; it can create duplicate live positions after a crash or callback miss. See [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:103), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:468), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:653), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1450).

**HIGH**
- A ghost or stranded half-position is still possible in the exit-drain path because `live.py` checks only the first exit leg’s exchange before deciding to `clear_ghost()` and skip the exit. For a two-exchange hedge, if leg 1 is gone but leg 2 still exists, this path wipes all strategy tracking and leaves the remaining leg unmanaged. See [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:2314) and [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:273).

**MEDIUM**
- The WS-2 success-side refactor is not actually wired. `BaseStrategy` now defines `handle_entry_success()` / `handle_exit_success()`, but `live.py` still calls only legacy `on_execution_success()` and only for `legs[0].symbol`, while `main.py`’s consumer callback calls no success callback at all. For FF this mostly works because of the legacy method, but FR’s `handle_exit_success()` is dead and its pending settlement state clears only by timeout, not on confirmed success. See [base.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/base.py:143), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1450), [funding_rate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/funding_rate.py:485), [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:1803).

**LOW**
- `_apply_engine_json_overrides()` is not complete beyond the three fields you added. The concrete current miss is `live_gate.continuous_enabled` from `engine.json`, which is not mapped into settings even though runtime reads `operational.live_gate_continuous_enabled`; changing it in `engine.json` has no effect. Other uncovered settings fields include `live_gate.min_exchange_health`, `live_gate.reevaluation_interval_hours`, `execution.rollback_timeout_ms`, `execution.reconciliation_interval_s`, and `risk.max_rollback_threshold`. Separately, `risk.circuit_breaker_mdd_threshold` is still not represented in `Settings` at all. See [engine.json](/Users/100aniv/Development/arbitrage_OMC/engine/config/engine.json:140), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:334), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:368), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:625), [live_gate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live_gate.py:115).


OpenAI Codex v0.115.0 (research preview)
--------
workdir: /Users/100aniv/Development/arbitrage_OMC/engine
model: gpt-5.4
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 019d998f-e7bb-76b0-9d16-ce53af46619f
--------
user
LEVIATHAN arbitrage engine — Review commits 3cfb65c, 1a5c80a, 80df207, cb0312d for structural refactoring correctness.

Context:
- WS-1 (3cfb65c): Config unification — removed trading.json deep-merge from config_loader.py, added 3 missing Pydantic overrides in config.py (_apply_engine_json_overrides: circuit_breaker_cooldown_seconds, circuit_breaker_api_error_rate_threshold, live_gate.mdd_threshold), replaced 3 direct engine.json file readers with get_config()/load_engine_config()
- WS-2 (3cfb65c): Pipeline decoupling — added 4 separate callbacks to BaseStrategy (handle_entry_rollback, handle_exit_rollback, handle_entry_success, handle_exit_success) + clear_ghost() method. Migrated 5 strategies (FF/FR/SF/StatArb/Triangular). Migrated all 4 on_execution_rollback call sites in live.py + main.py
- WS-3 (1a5c80a): PositionManager wired — asyncio.ensure_future(open_position/close_position) in _on_execution_result + live.py trade success path
- BUG-93 (80df207): LiveMode missing position_manager param — added it and wired at trade success
- BUG-94 (cb0312d): Optimistic write removal in futures_futures.py on_signal — new _pending_position_metadata dict replaces direct _open_positions write. Promotion happens in on_execution_success.

Review focus:
1. Can a ghost position STILL be created through any path?
2. Race conditions between on_signal (optimistic → pending), on_execution_success (pending → open), handle_entry_rollback (clear pending)?
3. What if on_execution_success is never called (e.g., live.py crash during execution)? Memory leak in _pending_position_metadata?
4. Is the new 4-callback semantics (entry vs exit rollback) correctly separated in live.py + main.py call sites?
5. Are Pydantic overrides complete (which ones might still be missing)?
6. Any correctness issue I might have overlooked?

Files to check:
- engine/src/strategies/futures_futures.py (BUG-94 key changes)
- engine/src/strategies/base.py (BaseStrategy 4-callback)
- engine/src/modes/live.py (trade_executed + rollback paths + position_manager wiring)
- engine/src/main.py (_on_execution_result + PositionManager init)
- engine/src/core/config.py (_apply_engine_json_overrides)

Return: CRITICAL/HIGH/MEDIUM/LOW findings with file:line references.
mcp: chrome-devtools starting
mcp: chrome-devtools ready
mcp startup: ready: chrome-devtools
codex
Reviewing the current tree and the four referenced commits against the specific failure modes you listed. I’m starting with the touched files plus the exact diffs so I can trace whether any execution path still leaves stale or ghost state behind.
exec
/bin/zsh -lc 'git rev-parse --short HEAD' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
d28166b
exec
/bin/zsh -lc 'git status --short' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
 M ../.claude/CLAUDE.md
 M ../.claude/agents/browser-verifier.md
 M ../.claude/agents/shadow-tester.md
 M ../.claude/commands/leviathan.md
 M ../.omc/project-memory.json
 M .omc/project-memory.json
 M .omc/state/agent-replay-d1dfbee9-40b6-41a8-aa82-06f508cfb187.jsonl
 M .omc/state/idle-notif-cooldown.json
 M .omc/state/last-tool-error.json
 M .omc/state/mission-state.json
 M .omc/state/sessions/d1dfbee9-40b6-41a8-aa82-06f508cfb187/ralph-state.json
 M .omc/state/sessions/d1dfbee9-40b6-41a8-aa82-06f508cfb187/ultrawork-state.json
 M .omc/state/subagent-tracking.json
 M config/strategy_params.json
?? ../.claude/position-state-mapping.md
?? ../.claude/scheduled_tasks.lock
?? ../.omc/config-architecture-analysis.md
?? ../.omc/sessions/0da77038-ce63-4a29-9b64-b5fbdcedc252.json
?? ../.omc/sessions/1e2fc5ce-2cbb-4e6e-8f39-b0883a992450.json
?? ../.omc/sessions/3a4fa292-f13d-4818-a56f-9d309ed83da8.json
exec
/bin/zsh -lc 'git show --stat --oneline 3cfb65c 1a5c80a 80df207 cb0312d' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
3cfb65c refactor(phoenix): WS-1 Config 단일화 + WS-2 Pipeline 분리
 engine/config/engine.json                       | 39 +++------------------
 engine/src/collectors/funding_rate_collector.py | 11 +++---
 engine/src/core/config.py                       | 25 ++++++++++++++
 engine/src/core/config_loader.py                | 21 +++--------
 engine/src/core/engine.py                       | 10 +++---
 engine/src/main.py                              | 16 ++++-----
 engine/src/modes/live.py                        | 38 +++++++++++++-------
 engine/src/strategies/base.py                   | 31 +++++++++++++++++
 engine/src/strategies/funding_rate.py           | 34 +++++++++++++-----
 engine/src/strategies/futures_futures.py        | 44 +++++++++++++++++++++++
 engine/src/strategies/spot_futures.py           | 46 +++++++++++++++++--------
 engine/src/strategies/statistical_arb.py        | 19 ++++++----
 engine/src/strategies/triangular.py             | 10 +++++-
 13 files changed, 231 insertions(+), 113 deletions(-)
1a5c80a refactor(phoenix): WS-3 PositionManager 실행 경로 연결 + 롤백 누수 수정
 engine/src/main.py                  | 46 +++++++++++++++++++++++++
 engine/src/risk/position_manager.py | 69 ++++++++++++++++++++++---------------
 2 files changed, 87 insertions(+), 28 deletions(-)
80df207 fix(phoenix): BUG-93 PositionManager not wired in live.py execution path
exec
/bin/zsh -lc 'rg -n "on_execution_rollback|handle_entry_rollback|handle_exit_rollback|handle_entry_success|handle_exit_success|clear_ghost|_pending_position_metadata|position_manager|_apply_engine_json_overrides|live_gate|circuit_breaker" src' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 382ms:
src/risk/circuit_breaker.py:175:            "circuit_breaker_open",
src/risk/circuit_breaker.py:204:                    "circuit_breaker_half_open",
src/risk/circuit_breaker.py:222:            "circuit_breaker_closed",
src/workflow/sit3_gate.py:199:                        cb = data.get("circuit_breaker", "UNKNOWN")
src/modes/preflight.py:80:        circuit_breaker_state: Callable[] -> str — returns "CLOSED"/"OPEN"/…
src/modes/preflight.py:81:        live_gate_check:      Optional async Callable[] -> bool — returns eligible.
src/modes/preflight.py:96:        circuit_breaker_state: Callable[[], str],
src/modes/preflight.py:97:        live_gate_check: Callable[[], Any] | None = None,
src/modes/preflight.py:104:        self._circuit_breaker_state = circuit_breaker_state
src/modes/preflight.py:105:        self._live_gate_check = live_gate_check
src/modes/preflight.py:134:            self._check_circuit_breaker(),
src/modes/preflight.py:135:            self._check_live_gate(),
src/modes/preflight.py:455:    async def _check_circuit_breaker(self) -> PreflightCheck:
src/modes/preflight.py:459:            state = self._circuit_breaker_state()
src/modes/preflight.py:470:            logger.warning("preflight_circuit_breaker_check_error", error=str(exc))
src/modes/preflight.py:483:    async def _check_live_gate(self) -> PreflightCheck:
src/modes/preflight.py:486:        if self._live_gate_check is None:
src/modes/preflight.py:492:                detail="live_gate_check callable not provided",
src/modes/preflight.py:497:            result = self._live_gate_check()
src/modes/preflight.py:509:            logger.warning("preflight_live_gate_check_error", error=str(exc))
codex
I’ve confirmed the worktree is dirty, so I’m treating this as read-only review against the current `d28166b` state plus the four target commits. Next I’m reading the exact control-flow in `live.py`, `main.py`, `futures_futures.py`, `base.py`, and the config override layer with line numbers.
exec
/bin/zsh -lc "nl -ba src/strategies/base.py | sed -n '1,240p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '1360,1760p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
     1	"""Base protocol and abstractions for all arbitrage strategies."""
     2	from __future__ import annotations
     3	
     4	import inspect
     5	from abc import ABC, abstractmethod
     6	from decimal import Decimal
     7	from typing import Any, Optional, Protocol, runtime_checkable
     8	
     9	from pydantic import BaseModel, Field
    10	
    11	from src.core.models import OrderSide, OrderType, Signal, Trade
    12	
    13	
    14	class TradeLeg(BaseModel):
    15	    """A single leg of a multi-leg trade."""
    16	
    17	    exchange_id: str
    18	    symbol: str
    19	    side: OrderSide
    20	    size: Decimal
 succeeded in 0ms:
  1360	                        try:
  1361	                            await self._telegram.send_alert_kr("rollback_failed", {
  1362	                                "strategy": sid,
  1363	                                "status": str(exec_result.status),
  1364	                                "mode": self._execution_mode,
  1365	                            })
  1366	                        except Exception:
  1367	                            pass
  1368	                    # DEFENSE-IN-DEPTH: ROLLBACK_FAILED = stranded position on exchange.
  1369	                    # BUG-84's strategy-level guard prevents re-entry via _open_positions, but
  1370	                    # set LiveMode-level symbol cooldown as backup (e.g. on strategy restart).
  1371	                    # Cover ALL legs — spot_futures has legs[0]=spot, legs[1]=futures with
  1372	                    # different symbols; only cooling legs[0] leaves the futures leg unprotected.
  1373	                    if exec_result.status == ExecutionStatus.ROLLBACK_FAILED:
  1374	                        _rf_now = time.monotonic()
  1375	                        _rf_symbols = [l.symbol for l in trade_request.legs if l.symbol]
  1376	                        for _rf_sym in _rf_symbols:
  1377	                            self._symbol_last_trade[_rf_sym] = _rf_now
  1378	                        if _rf_symbols:
  1379	                            logger.warning(
exec
/bin/zsh -lc "nl -ba src/strategies/futures_futures.py | sed -n '1,420p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
     1	"""Futures-Futures Cross strategy (CEX-CEX).
     2	
     3	Price discrepancy between the same futures contract on two exchanges.
     4	Similar to cross-exchange spot but operates on leveraged futures positions.
     5	
     6	US-233: Tighter parameters — min_spread_bps=15, min_book_depth_usd=500, max_notional_usd=200.
     7	"""
     8	from __future__ import annotations
     9	
    10	import logging
    11	import os
    12	import time
    13	from decimal import Decimal
    14	from typing import Any, Optional
    15	
    16	logger = logging.getLogger(__name__)
    17	
    18	# rollback_no_state 누적 횟수가 이 임계값 이상이면 CRITICAL 로그 발생 (운영자 조치 요망)
    19	_ROLLBACK_NO_STATE_ALERT_THRESHOLD = 3
    20	
exec
/bin/zsh -lc "nl -ba src/main.py | sed -n '1810,2125p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/main.py | sed -n '1530,1565p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/core/config.py | sed -n '625,740p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '1880,2365p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   625	def _apply_engine_json_overrides(settings: Settings) -> None:
   626	    """Override Pydantic defaults with engine.json runtime values.
   627	
   628	    engine.json is the single source of truth for runtime configuration.
   629	    .env / Pydantic handles secrets (API keys) only.
   630	    Logs every override for traceability.
   631	    """
   632	    ecfg = load_engine_config()
   633	    if not ecfg:
   634	        return
   635	
   636	    _log = logging.getLogger(__name__)
   637	    _overrides: list[str] = []
   638	
   639	    # --- exchanges.active → trading.active_exchanges ---
   640	    _exchanges_active = ecfg.get("exchanges", {}).get("active")
   641	    if _exchanges_active and _exchanges_active != settings.trading.active_exchanges:
   642	        _overrides.append(
   643	            f"trading.active_exchanges: {settings.trading.active_exchanges} → {_exchanges_active}"
   644	        )
 succeeded in 0ms:
  1530	                logger.warning("ExposureTracker: Redis unavailable, using in-memory fallback")
  1531	        except Exception as exc:
  1532	            logger.warning("ExposureTracker init failed (non-fatal): %s", exc)
  1533	
  1534	    # ------------------------------------------------------------------
  1535	    # Step 7: Execution Engine
  1536	    # ------------------------------------------------------------------
  1537	
  1538	    async def _init_execution(self) -> None:
  1539	        from src.execution.executor import AtomicExecutor
  1540	        from src.execution.trade_consumer import TradeRequestConsumer
  1541	
  1542	        # US-236: Initialize PositionManager (in-memory tracking; dual_writer=None in shadow mode)
  1543	        try:
  1544	            from src.risk.position_manager import PositionManager
  1545	            self._position_manager = PositionManager(
  1546	                dual_writer=None,
  1547	                redis_client=getattr(self, "_redis_client", None),
  1548	            )
  1549	            logger.info("PositionManager initialized (dual_writer=None, shadow mode)")
 succeeded in 0ms:
  1880	                min_edge=_min_edge,
  1881	            )
  1882	        else:
  1883	            # Fallback: sequential single-leg execution
  1884	            logger.warning(
  1885	                "live_mode.fallback_sequential_execution orders=%d exchanges=%d",
  1886	                len(orders), len(exchanges_involved),
  1887	            )
  1888	            results = []
  1889	            for order in orders:
  1890	                # Single-leg: use execute_multi_leg with 1 order if available
  1891	                # BUG-92: execute_same_exchange(order, order) doubles the position.
  1892	                # execute_multi_leg always available on AtomicExecutor — use it.
  1893	                if hasattr(self._executor, 'execute_multi_leg'):
  1894	                    result = await self._executor.execute_multi_leg(
  1895	                        exchange_id=order.exchange_id,
  1896	                        orders=[order],
  1897	                        strategy_id=sid,
  1898	                    )
  1899	                else:
 succeeded in 0ms:
  1810	        # US-129: Update position tracking and peak equity for RiskGuardian PortfolioState
  1811	        if getattr(execution_result.status, "value", str(execution_result.status)) == "success":
  1812	            try:
  1813	                legs_info = [
  1814	                    (getattr(leg, "trade", None), getattr(leg, "order", None))
  1815	                    for leg in getattr(execution_result, "legs", [])
  1816	                ]
  1817	                for trade, order in legs_info:
  1818	                    if trade is not None and order is not None:
  1819	                        symbol = order.symbol
  1820	                        pos_value = trade.price * trade.amount
  1821	                        side = getattr(order.side, "value", str(order.side)).upper()
  1822	                        if side == "BUY":
  1823	                            self._position_sizes[symbol] = (
  1824	                                self._position_sizes.get(symbol, Decimal("0")) + pos_value
  1825	                            )
  1826	                        else:
  1827	                            current = self._position_sizes.get(symbol, Decimal("0"))
  1828	                            updated = max(Decimal("0"), current - pos_value)
  1829	                            if updated == Decimal("0"):
codex
The core paths are visible now. I’m drilling further into `futures_futures.on_signal`, success/rollback dispatch, and the config model definitions to check for missed override fields and any symbol-level race introduced by the pending-state split.
exec
/bin/zsh -lc "nl -ba src/strategies/futures_futures.py | sed -n '420,760p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   420	        if self.config.excluded_exchanges:
   421	            for _ex in (signal.buy_exchange, signal.sell_exchange):
   422	                if _ex and any(_ex == exc or _ex.startswith(exc) for exc in self.config.excluded_exchanges):
   423	                    self._metrics.signals_filtered += 1
   424	                    logger.debug(
   425	                        "strategy.rejected strategy=futures_futures reason=excluded_exchange exchange=%s", _ex
   426	                    )
   427	                    return None
   428	
   429	        # Bug 25a / Bug 31 prevention: same futures exchange on both legs.
   430	        # Must compare *resolved* futures exchange IDs — "bitget" and "bitget_futures"
   431	        # both resolve to "bitget_futures", so a raw-string comparison misses this case.
   432	        def _to_futures_id(eid: str) -> str:
   433	            return eid if eid.endswith("_futures") else f"{eid}_futures"
   434	
   435	        _buy_futures_id = _to_futures_id(signal.buy_exchange) if signal.buy_exchange else None
   436	        _sell_futures_id = _to_futures_id(signal.sell_exchange) if signal.sell_exchange else None
   437	        if _buy_futures_id and _sell_futures_id and _buy_futures_id == _sell_futures_id:
   438	            self._metrics.signals_filtered += 1
   439	            logger.debug(
exec
/bin/zsh -lc "nl -ba src/core/config.py | sed -n '240,625p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   240	
   241	    # Coinone (US-359)
   242	    coinone_access_token: str = Field(default="", alias="COINONE_ACCESS_TOKEN")
   243	    coinone_api_secret: str = Field(default="", alias="COINONE_API_SECRET")
   244	
   245	    # Tier4 — API 키 추후 발급, 어댑터는 Phase K에서 미리 구현 (US-359)
   246	    mexc_api_key: str = Field(default="", alias="MEXC_API_KEY")
   247	    mexc_api_secret: str = Field(default="", alias="MEXC_API_SECRET")
   248	    gateio_api_key: str = Field(default="", alias="GATEIO_API_KEY")
   249	    gateio_api_secret: str = Field(default="", alias="GATEIO_API_SECRET")
   250	    bingx_api_key: str = Field(default="", alias="BINGX_API_KEY")
   251	    bingx_api_secret: str = Field(default="", alias="BINGX_API_SECRET")
   252	    lbank_api_key: str = Field(default="", alias="LBANK_API_KEY")
   253	    lbank_api_secret: str = Field(default="", alias="LBANK_API_SECRET")
   254	    orangex_api_key: str = Field(default="", alias="ORANGEX_API_KEY")
   255	    orangex_api_secret: str = Field(default="", alias="ORANGEX_API_SECRET")
   256	
   257	
   258	class RiskSettings(BaseSettings):
   259	    model_config = SettingsConfigDict(env_prefix="RISK_")
exec
/bin/zsh -lc "git show 3cfb65c:engine/src/modes/live.py | nl -ba | sed -n '1380,1470p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "git show cb0312d:engine/src/strategies/futures_futures.py | nl -ba | sed -n '390,620p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1380	                            )
  1381	                    # HIGH-1: ROLLBACK_FAILED on EXIT order → restore _pending_exits snapshot.
  1382	                    # BUG-84 prohibition applies to ENTRY only (stranded entry = don't re-enter).
  1383	                    # Exit ROLLBACK_FAILED = partial exchange close failed, but internal tracking
  1384	                    # already moved pos to _pending_exits/_open_positions cleared. Must restore.
  1385	                    if exec_result.status == ExecutionStatus.ROLLBACK_FAILED:
  1386	                        _is_exit_rf = self._is_reduceonly_request(trade_request)
  1387	                        if _is_exit_rf and self._strategy_manager is not None:
  1388	                            _strat_rf = self._strategy_manager.get_strategy(sid)
  1389	                            # Only call for strategies with _pending_exits tracking (futures_futures).
  1390	                            # spot_futures/_funding_rate docstrings say "ROLLBACK_FAILED: do not call"
  1391	                            # and lack _pending_exits, so calling on them violates the contract.
  1392	                            if _strat_rf is not None:
  1393	                                for _rf_sym_ex in {l.symbol for l in trade_request.legs if l.symbol}:
  1394	                                    try:
  1395	                                        # WS-2.6: Exit rollback = position still on exchange → restore
  1396	                                        _strat_rf.handle_exit_rollback(_rf_sym_ex)
  1397	                                        logger.warning(
  1398	                                            "live_mode.exit_rollback_failed_pending_restored "
  1399	                                            "symbol=%s — stranded! verify exchange positions.",
 succeeded in 0ms:
   390	    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
   391	        self._metrics.signals_received += 1
   392	
   393	        if not self._is_active:
   394	            self._metrics.signals_filtered += 1
   395	            return None
   396	
   397	        # US-273: Stale Guard — fail closed if book_age_ms missing or stale
   398	        if self.config.enable_stale_guard:
   399	            raw_book_age = signal.metadata.get("book_age_ms")
   400	            if raw_book_age is None:
   401	                logger.warning("missing book_age_ms, filtering signal")
   402	                self._metrics.signals_filtered += 1
   403	                return None
   404	            book_age_ms = float(raw_book_age)
   405	            if book_age_ms / 1000 > self.config.max_book_age_seconds:
   406	                self._metrics.signals_filtered += 1
   407	                return None
   408	
   409	        # PHOENIX: Exclude specific symbols (e.g. stranded position symbols)
exec
/bin/zsh -lc "git show 3cfb65c:engine/src/main.py | nl -ba | sed -n '2088,2110p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc 'git show cb0312d''^:engine/src/strategies/futures_futures.py | nl -ba | sed -n '"'390,620p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  2088	                    "size": float(trade_request.legs[0].size) if trade_request.legs else 0,
  2089	                    "pnl": float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt),
  2090	                    "timestamp": datetime.now(timezone.utc).isoformat(),
  2091	                }
  2092	                asyncio.ensure_future(self._trade_bot.send_fill_kr(fill_data))
  2093	            except Exception:
  2094	                pass  # Non-critical: Telegram fill notification failure
  2095	
  2096	    async def _rebalancer_loop(self) -> None:
  2097	        """US-120: Periodic inventory rebalancing check + Telegram alert."""
  2098	        while self.state.running:
  2099	            try:
  2100	                await asyncio.sleep(self._rebalancer.check_interval_s)
  2101	
  2102	                if self._rebalancer.has_critical_imbalance() and self._telegram:
  2103	                    try:
  2104	                        await self._telegram.send_alert_kr(
  2105	                            "inventory_critical", {},
  2106	                        )
  2107	                    except Exception:
 succeeded in 0ms:
   390	                self._metrics.signals_filtered += 1
   391	                return None
   392	            book_age_ms = float(raw_book_age)
   393	            if book_age_ms / 1000 > self.config.max_book_age_seconds:
   394	                self._metrics.signals_filtered += 1
   395	                return None
   396	
   397	        # PHOENIX: Exclude specific symbols (e.g. stranded position symbols)
   398	        if self.config.excluded_symbols and signal.symbol:
   399	            _base = signal.symbol.split("/")[0].upper()
   400	            if _base in {s.upper() for s in self.config.excluded_symbols}:
   401	                self._metrics.signals_filtered += 1
   402	                logger.debug(
   403	                    "strategy.rejected strategy=futures_futures reason=excluded_symbol symbol=%s", signal.symbol
   404	                )
   405	                return None
   406	
   407	        # CB: Exclude KRW spot-only exchanges (no futures market)
   408	        if self.config.excluded_exchanges:
   409	            for _ex in (signal.buy_exchange, signal.sell_exchange):
exec
/bin/zsh -lc "rg -n \"handle_entry_success\\(|handle_exit_success\\(|on_execution_success\\(\" src" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/modes/live.py:1456:                        _strat_s.on_execution_success(_success_symbol)
src/strategies/base.py:143:    def handle_entry_success(self, symbol: str) -> None:
src/strategies/base.py:146:    def handle_exit_success(self, symbol: str) -> None:
src/infra/exchange/native_bitget.py:486:        # executor calls on_execution_success(), position is removed from tracking,
src/strategies/futures_futures.py:219:    def on_execution_success(self, symbol: str) -> None:
src/strategies/futures_futures.py:265:    def handle_entry_success(self, symbol: str) -> None:
src/strategies/futures_futures.py:268:    def handle_exit_success(self, symbol: str) -> None:
src/strategies/spot_futures.py:391:    def handle_entry_success(self, symbol: str) -> None:
src/strategies/spot_futures.py:394:    def handle_exit_success(self, symbol: str) -> None:
src/strategies/spot_futures.py:407:    def on_execution_success(self, symbol: str) -> None:
src/strategies/funding_rate.py:482:    def handle_entry_success(self, symbol: str) -> None:
src/strategies/funding_rate.py:485:    def handle_exit_success(self, symbol: str) -> None:
codex
I’ve confirmed one structural mismatch already: the new success callbacks exist, but the live execution path still dispatches only legacy `on_execution_success()` and only for `legs[0].symbol`. I’m checking whether that is harmless via legacy shims in the migrated strategies or whether it leaves symbol/state cleanup incomplete.
exec
/bin/zsh -lc "nl -ba src/strategies/funding_rate.py | sed -n '455,505p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/strategies/spot_futures.py | sed -n '360,430p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   455	            "fr.inject_position symbol=%s sell_exchange=%s buy_exchange=%s size=%s",
   456	            symbol, metadata.get("sell_exchange"), metadata.get("buy_exchange"),
   457	            metadata.get("size"),
   458	        )
   459	
   460	    def on_execution_rollback(self, symbol: str) -> None:
   461	        """Legacy — delegates to handle_entry_rollback for backward compat."""
   462	        self.handle_entry_rollback(symbol)
   463	
   464	    # WS-2: Separated lifecycle callbacks
   465	    def handle_entry_rollback(self, symbol: str) -> None:
   466	        """Entry rolled back → clear tracking."""
   467	        if symbol in self._open_positions:
   468	            logger.info("fr.entry_rollback_cleared symbol=%s", symbol)
   469	            self._open_positions.pop(symbol, None)
   470	
   471	    def handle_exit_rollback(self, symbol: str) -> None:
   472	        """Settlement exit rolled back → restore for retry."""
   473	        if symbol in self._pending_settlement_positions:
   474	            logger.warning(
 succeeded in 0ms:
   360	        # US-271/US-322: Remove closed position on exit fill
   361	        # Handles both spot and futures leg symbols via reverse lookup
   362	        if self._holding_timeout_enabled:
   363	            meta = getattr(trade, "metadata", {}) or {}
   364	            if meta.get("leg_type", "").startswith("timeout_close"):
   365	                resolved = self._resolve_spot_symbol(trade.symbol)
   366	                if resolved:
   367	                    self._open_positions.pop(resolved, None)
   368	                    self._pending_close_symbols.discard(resolved)  # BUG-91: clear pending flag
   369	
   370	    def on_execution_rollback(self, symbol: str) -> None:
   371	        """Legacy — delegates to entry rollback for backward compat."""
   372	        resolved = self._resolve_spot_symbol(symbol) or symbol
   373	        if resolved in self._pending_close_symbols:
   374	            self.handle_exit_rollback(symbol)
   375	        else:
   376	            self.handle_entry_rollback(symbol)
   377	
   378	    # WS-2: Separated lifecycle callbacks
   379	    def handle_entry_rollback(self, symbol: str) -> None:
exec
/bin/zsh -lc "nl -ba src/strategies/triangular.py | sed -n '240,260p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/strategies/statistical_arb.py | sed -n '1048,1085p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   240	    async def on_fill(self, trade: Trade) -> None:
   241	        await super().on_fill(trade)
   242	        # BUG-80: clear inflight path on fill so the path can be re-entered next tick
   243	        self._inflight_paths = {pk for pk in self._inflight_paths if trade.symbol not in pk}
   244	
   245	    def on_execution_rollback(self, symbol: str) -> None:
   246	        """Legacy — delegates to handle_entry_rollback."""
   247	        self.handle_entry_rollback(symbol)
   248	
   249	    def handle_entry_rollback(self, symbol: str) -> None:
   250	        """Clear inflight path on rollback to prevent permanent lockout."""
   251	        self._inflight_paths = {pk for pk in self._inflight_paths if symbol not in pk}
   252	
   253	    def clear_ghost(self, symbol: str) -> None:
   254	        """Same as entry rollback for Triangular."""
   255	        self.handle_entry_rollback(symbol)
exec
/bin/zsh -lc "nl -ba config/engine.json | sed -n '1,240p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1048	        return []
  1049	
  1050	    def on_execution_rollback(self, symbol: str) -> None:
  1051	        """Legacy — delegates to handle_entry_rollback."""
  1052	        self.handle_entry_rollback(symbol)
  1053	
  1054	    # WS-2: Separated lifecycle callbacks
  1055	    def handle_entry_rollback(self, symbol: str) -> None:
  1056	        """Entry rolled back → reset pair states to FLAT."""
  1057	        for (sym_a, sym_b), ps in self._pair_states.items():
  1058	            if symbol in (sym_a, sym_b) and ps.state != StatArbState.FLAT:
  1059	                logger.info(
  1060	                    "stat_arb.rollback_reset pair=(%s,%s) prev_state=%s",
  1061	                    sym_a, sym_b, ps.state,
  1062	                )
  1063	                ps.state = StatArbState.FLAT
  1064	                ps.bars_in_position = 0
  1065	                _pk = (sym_a, sym_b)
  1066	                self._pair_entry_spread.pop(_pk, None)
  1067	                self._pair_entry_notional.pop(_pk, None)
 succeeded in 0ms:
     1	{
     2	  "_comment": "LEVIATHAN Engine Configuration \u2014 Runtime settings (non-secret). Secrets stay in .env",
     3	  "mode": "live",
     4	  "env": "dev",
     5	  "capital": {
     6	    "allocation_mode": "percentage",
     7	    "reserve_pct": 20,
     8	    "strategies": {
     9	      "funding_rate": {
    10	        "allocation_pct": 35
    11	      },
    12	      "futures_futures": {
    13	        "allocation_pct": 20
    14	      },
    15	      "spot_futures": {
    16	        "allocation_pct": 20
    17	      },
    18	      "cross_exchange": {
    19	        "allocation_pct": 25
    20	      }
exec
/bin/zsh -lc 'rg -n "min_exchange_health|reevaluation_interval_hours|max_single_trade_pct|max_exposure_pct|exchange_health_threshold|max_volatility_multiple|max_rollback_threshold|rollback_timeout_ms|reconciliation_interval_s" config/engine.json src/main.py src/modes/live_gate.py src/modes/live.py src/core/config_loader.py src/core/config.py' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
config/engine.json:96:    "max_rollback_threshold": 0.02,
src/modes/live_gate.py:115:            self.MIN_EXCHANGE_HEALTH = float(lg.min_exchange_health)
src/modes/live_gate.py:117:            self.REEVALUATION_INTERVAL_HOURS = lg.reevaluation_interval_hours
src/core/config.py:269:    max_single_trade_pct: Decimal = Field(
src/core/config.py:273:    max_exposure_pct: Decimal = Field(
src/core/config.py:281:    exchange_health_threshold: Decimal = Field(default=Decimal("0.9"))
src/core/config.py:282:    max_volatility_multiple: Decimal = Field(
src/core/config.py:286:    max_rollback_threshold: Decimal = Field(
src/core/config.py:294:        "max_single_trade_pct",
src/core/config.py:295:        "max_exposure_pct",
src/core/config.py:354:    min_exchange_health: Decimal = Field(
src/core/config.py:358:    reevaluation_interval_hours: int = Field(
src/core/config.py:377:    rollback_timeout_ms: int = Field(
src/core/config.py:382:    reconciliation_interval_s: int = Field(
src/core/config.py:491:    reconciliation_interval_s: float = Field(default=5.0, alias="RECONCILIATION_INTERVAL_S")
src/main.py:396:            ("rollback_timeout_ms", "ROLLBACK_TIMEOUT_MS"),
src/main.py:397:            ("reconciliation_interval_s", "RECONCILIATION_INTERVAL_S"),
src/main.py:409:        if "max_rollback_threshold" in risk:
src/main.py:410:            _setdefault("RISK_MAX_ROLLBACK_THRESHOLD", risk["max_rollback_threshold"])
src/main.py:1404:            # BUG-100: max_single_trade_pct must match the largest per-strategy trade cap.
exec
/bin/zsh -lc "nl -ba src/main.py | sed -n '372,418p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   372	        self._shutdown_event.set()
   373	
   374	    # ------------------------------------------------------------------
   375	    # Step 1: Configuration
   376	    # ------------------------------------------------------------------
   377	
   378	    @staticmethod
   379	    def _apply_trading_json_defaults(cfg: dict) -> None:
   380	        """Inject trading.json values as env var defaults (env vars take priority)."""
   381	
   382	        def _setdefault(key: str, value: object) -> None:
   383	            if key not in os.environ:
   384	                os.environ[key] = json.dumps(value) if isinstance(value, list) else str(value)
   385	
   386	        if "active_exchanges" in cfg:
   387	            _setdefault("TRADING_ACTIVE_EXCHANGES", cfg["active_exchanges"])
   388	
   389	        sym = cfg.get("symbol_discovery", {})
   390	        if "min_exchanges" in sym:
   391	            _setdefault("TRADING_SYMBOL_MIN_EXCHANGES", sym["min_exchanges"])
exec
/bin/zsh -lc 'rg -n "continuous_enabled|live_gate_continuous_enabled|LIVE_GATE_CONTINUOUS_ENABLED|LIVE_GATE_CONTINUOUS" src/main.py src/modes/live_gate.py src/core/config.py' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/main.py:2429:            if _op.live_gate_continuous_enabled:
src/modes/live_gate.py:342:        Enabled via LIVE_GATE_CONTINUOUS_ENABLED env var (default True).
src/modes/live_gate.py:345:        if not _op.live_gate_continuous_enabled:
src/core/config.py:504:    live_gate_continuous_enabled: bool = Field(default=True, alias="LIVE_GATE_CONTINUOUS_ENABLED")
src/core/config.py:561:    live_gate_continuous_raw: str = Field(default="1", alias="LIVE_GATE_CONTINUOUS")
exec
/bin/zsh -lc "rg -n \"get_settings\\(\\)\\.risk\\.|settings\\.risk\\.|risk\\.\" src/main.py src/modes/live.py src/modes/live_gate.py src/risk src/core | head -n 200" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/modes/live_gate.py:496:            from src.risk.kill_switch import is_halted
src/modes/live_gate.py:642:            from src.risk.kill_switch import halt_local, clear_halt, is_halted  # noqa: PLC0415
src/modes/live.py:261:                _gc_slip("risk.max_cumulative_slippage_bps") or 50.0
src/modes/live.py:264:                _gc_slip("risk.slippage_window_trades") or 10
src/modes/live.py:1176:            from src.risk.kill_switch import halt_local
src/main.py:492:            val = risk.get(key)
src/main.py:495:                    "CONFIG: risk.%s=%s is outside valid range [0, 100]", label, val,
src/main.py:1353:            from src.risk.circuit_breaker import CircuitBreaker
src/main.py:1369:            _cb_mdd = float(_gc_cb("risk.circuit_breaker_mdd_threshold", default=0.02))
src/main.py:1370:            _cb_loss = int(_gc_cb("risk.circuit_breaker_consecutive_loss_limit", default=5))
src/main.py:1371:            _cb_err = float(_gc_cb("risk.circuit_breaker_api_error_rate_threshold", default=0.20))
src/main.py:1372:            _cb_cool = float(_gc_cb("risk.circuit_breaker_cooldown_seconds", default=300.0))
src/main.py:1373:            _cb_half = int(_gc_cb("risk.circuit_breaker_half_open_test_count", default=3))
src/main.py:1388:            from src.risk.guardian import RiskGuardian
src/main.py:1428:                _rg_gc("risk.max_net_exposure_pct", default=30)
src/main.py:1431:                _rg_gc("risk.max_rollback_threshold", default=0.02)
src/main.py:1436:            _rg_warmup = float(_rg_gc("risk.warmup_seconds", default=120.0))
src/main.py:1466:            from src.risk.per_strategy_cb import PerStrategyCB
src/main.py:1476:            from src.risk.correlation_monitor import CorrelationMonitor
src/main.py:1477:            _corr_window = int(_rg_gc("risk.correlation_window", default=30))
exec
/bin/zsh -lc "nl -ba src/main.py | sed -n '1760,1815p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1760	
  1761	            # Gross exposure = net directional + capital tied in cross-exchange hedges
  1762	            _total_exposure = used_capital + self._cross_gross_exposure
  1763	
  1764	            # US-175/Amendment 7: populate net_exposures from ExposureTracker snapshot.
  1765	            # snapshot() is synchronous and always reflects latest fills in this process.
  1766	            _net_exposures = (
  1767	                self._exposure_tracker.snapshot()
  1768	                if self._exposure_tracker is not None
  1769	                else {}
  1770	            )
  1771	
  1772	            portfolio = PortfolioState(
  1773	                total_capital=capital_total,
  1774	                used_capital=used_capital,
  1775	                current_drawdown_pct=current_drawdown_pct,
  1776	                total_exposure=_total_exposure,
  1777	                position_sizes=_effective_positions,
  1778	                exchange_health_scores=exchange_health,
  1779	                volatility_1min={},   # populated when live vol data available
exec
/bin/zsh -lc "nl -ba src/risk/position_manager.py | sed -n '1,280p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
     1	"""LEVIATHAN Position Manager.
     2	
     3	Tracks all open positions per (strategy_id, exchange_id, symbol).
     4	Maintains net exposure per (exchange, base_asset) in Redis.
     5	Provides real-time PnL calculation (unrealized + realized).
     6	
     7	Position lifecycle: OPEN → UPDATE → CLOSE
     8	Every state change is dual-written to PostgreSQL WAL + Redis.
     9	"""
    10	from __future__ import annotations
    11	
    12	from dataclasses import dataclass, field
    13	from decimal import Decimal
    14	from typing import Any
    15	
    16	import structlog
    17	
    18	logger = structlog.get_logger(__name__)
    19	
    20	# Redis key templates
exec
/bin/zsh -lc "rg -n \"_on_execution_result\\(\" -n src/main.py src" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/main.py:1803:    def _on_execution_result(self, trade_request, execution_result) -> None:
src/main.py:1803:    def _on_execution_result(self, trade_request, execution_result) -> None:
codex
I found the main semantic gap in the rollback refactor: both `live.py` and `main.py` still treat every `ROLLED_BACK`/`REJECTED` as an entry failure. I’m verifying whether any execution path can feed an exit request into those branches, because if it can, the refactor is still structurally incorrect for exit rollback recovery.
exec
/bin/zsh -lc "rg -n \"ExecutionStatus\\.ROLLED_BACK|ExecutionStatus\\.REJECTED|rolled_back|rejected\" src/execution src/modes/live.py src/main.py | head -n 250" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/modes/live.py:115:    trades_rejected: int = 0
src/modes/live.py:469:                logger.warning("live_mode.approval_rejected — aborting live start")
src/modes/live.py:470:                raise LiveGateFailed("Live approval rejected or timed out")
src/modes/live.py:971:                        logger.debug("live_mode.data_quality_rejected exchange=%s symbol=%s", exchange_id, symbol)
src/modes/live.py:1206:                    logger.info("live_mode.risk_rejected strategy=%s", sid)
src/modes/live.py:1307:                _impact_rejected = False
src/modes/live.py:1324:                            "live_mode.market_impact_rejected strategy=%s exchange=%s "
src/modes/live.py:1329:                        _impact_rejected = True
src/modes/live.py:1331:                if _impact_rejected:
src/modes/live.py:1414:                    if exec_result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.REJECTED):
src/modes/live.py:1439:                        if exec_result.status == ExecutionStatus.ROLLED_BACK and trade_request.legs:
src/modes/live.py:1676:                hasattr(exec_result, 'status') and str(exec_result.status) not in ('rolled_back', 'rejected', 'rollback_failed')
src/main.py:2082:                elif status_val in ("rolled_back", "rollback_failed", "timeout"):
src/main.py:2085:                # else: "rejected" = infrastructure reject (no adapter, halted, health)
src/main.py:2093:        if getattr(execution_result.status, "value", str(execution_result.status)) in ("rolled_back", "rejected"):
src/main.py:2099:                        # WS-2.6: Use handle_entry_rollback (rolled_back/rejected = entry failed)
src/main.py:3006:                        logger.warning("HMM model rejected: not fitted after training")
src/main.py:3069:                        "XGBoost model rejected: best_score=%.4f < %.2f",
src/main.py:3478:            # 150s and drop to health_score=0.6 → livelock (all trades rejected).
src/main.py:3505:                "Health OK — trades: processed=%d success=%d rejected=%d",
exec
/bin/zsh -lc "rg -n \"_on_execution_result|on_execution_result|execution_result_callback|result_callback|callback=.*_on_execution_result|_execute_trade_request\\(\" src" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/main.py:1566:            on_result=self._on_execution_result,
src/main.py:1803:    def _on_execution_result(self, trade_request, execution_result) -> None:
src/main.py:1834:                # _on_execution_result is sync → fire-and-forget via ensure_future
src/modes/live.py:1061:                await self._execute_trade_request(request)
src/modes/live.py:1089:    async def _execute_trade_request(self, trade_request: TradeRequest) -> None:
src/modes/live.py:1773:            await self._execute_trade_request(request)
src/modes/live.py:2352:                                    self._execute_trade_request(_exit_req),
exec
/bin/zsh -lc 'rg -n "reduceOnly|settlement_close|timeout_close|spread_exit_close|time_exit_close|futures_close" src/strategies src/modes/live.py src/main.py' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/main.py:1674:                        metadata={"reduceOnly": True, "leg_type": "reconciler_auto_close"},
src/main.py:1741:            # Settlement exits and reduceOnly closes bypass risk checks (early return — skip PortfolioState)
src/main.py:1744:                    leg.metadata.get("reduceOnly") is True or
src/main.py:1745:                    str(leg.metadata.get("leg_type", "")).startswith("settlement_close")
src/main.py:1838:                            o.metadata.get("reduceOnly") is True or
src/main.py:1839:                            str(o.metadata.get("leg_type", "")).startswith(("settlement_close", "timeout_close"))
src/main.py:1871:                        order.metadata.get("reduceOnly") is True or
src/main.py:1872:                        str(order.metadata.get("leg_type", "")).startswith("settlement_close")
src/main.py:2229:                    metadata={"reduceOnly": True},
src/modes/live.py:597:                                                "reduceOnly": "true",
src/modes/live.py:1218:        # bypass cooldown for mixed orders where only one leg is reduceOnly.
src/modes/live.py:1303:            # max_market_impact_bps. Exit (reduceOnly) orders are exempt.
src/modes/live.py:1783:        """Return True if all legs are reduceOnly (exit/close order).
src/modes/live.py:1789:            leg.metadata.get("reduceOnly") for leg in trade_request.legs
src/modes/live.py:1945:        # with only one reduceOnly leg must not be misclassified as close.
src/strategies/futures_futures.py:319:                                    metadata={"leg_type": "spread_exit_close_long", "reduceOnly": True},
src/strategies/futures_futures.py:328:                                    metadata={"leg_type": "spread_exit_close_short", "reduceOnly": True},
src/strategies/futures_futures.py:360:                                    metadata={"leg_type": "time_exit_close_long", "reduceOnly": True},
src/strategies/futures_futures.py:369:                                    metadata={"leg_type": "time_exit_close_short", "reduceOnly": True},
src/strategies/futures_futures.py:715:            "futures_close", "spread_exit_close_long", "spread_exit_close_short",
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '1778,1795p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1778	    # Executor routing helpers
  1779	    # -----------------------------------------------------------------------
  1780	
  1781	    @staticmethod
  1782	    def _is_reduceonly_request(trade_request: TradeRequest) -> bool:
  1783	        """Return True if all legs are reduceOnly (exit/close order).
  1784	
  1785	        Used to bypass strategy_filter, cooldown, and dedup restrictions
  1786	        that must not block position-close orders.
  1787	        """
  1788	        return bool(trade_request.legs) and all(
  1789	            leg.metadata.get("reduceOnly") for leg in trade_request.legs
  1790	        )
  1791	
  1792	    def _legs_to_orders(self, trade_request: TradeRequest) -> list[Order]:
  1793	        """Convert TradeRequest legs to Order objects.
  1794	
  1795	        Limit order fallback: when orderbook spread > limit_fallback_spread_bps,
exec
/bin/zsh -lc "nl -ba src/strategies/funding_rate.py | sed -n '90,170p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
    90	        # allowing new entries on symbols whose exits haven't confirmed yet.
    91	        self._settlement_cooldown_until: float = 0.0  # monotonic timestamp
    92	
    93	    def _minutes_to_next_settlement(self, now_utc: datetime | None = None) -> float:
    94	        """Return minutes until next funding settlement (UTC 00/08/16).
    95	
    96	        Used by on_signal to restrict entries to the settlement window.
    97	        """
    98	        if now_utc is None:
    99	            now_utc = datetime.now(timezone.utc)
   100	        hours_since_midnight = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0
   101	        min_hours_before = min(
   102	            ((sh - hours_since_midnight) % 24) for sh in self.config.settlement_hours
   103	        )
   104	        return min_hours_before * 60.0
   105	
   106	    def _check_settlement_release(self) -> None:
   107	        """Auto-release all positions after a settlement hour passes.
   108	
   109	        BUG-74: Queues exit TradeRequests before clearing _open_positions so
exec
/bin/zsh -lc 'rg -n "pending_settlement_positions|on_fill|on_execution_success" src/strategies/funding_rate.py' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
86:        self._pending_settlement_positions: dict[str, Any] = {}  # symbol → pos dict
149:                    self._pending_settlement_positions[symbol] = pos
208:        # Check both _open_positions AND _pending_settlement_positions: after _check_settlement_release
209:        # moves positions to _pending_settlement_positions, the symbol is absent from _open_positions
211:        if signal.symbol in self._open_positions or signal.symbol in self._pending_settlement_positions:
217:                list(self._pending_settlement_positions.keys()),
406:    async def on_fill(self, trade: Trade) -> None:
407:        await super().on_fill(trade)
415:        Issue#4 fix: _pending_settlement_positions retains position data until
417:        generous. After timeout, if positions are still in _pending_settlement_positions,
424:        elif self._pending_settlement_positions:
429:                    list(self._pending_settlement_positions.keys()), elapsed,
431:                self._pending_settlement_positions.clear()
437:                    list(self._pending_settlement_positions.keys()),
473:        if symbol in self._pending_settlement_positions:
477:            restored = self._pending_settlement_positions.pop(symbol)
487:        self._pending_settlement_positions.pop(symbol, None)
492:        self._pending_settlement_positions.pop(symbol, None)
exec
/bin/zsh -lc "nl -ba src/strategies/funding_rate.py | sed -n '420,500p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   420	        """
   421	        if self._pending_exit_requests:
   422	            # Exits are queued — record routing timestamp and return them
   423	            self._settlement_routed_at = time.monotonic()
   424	        elif self._pending_settlement_positions:
   425	            elapsed = time.monotonic() - self._settlement_routed_at
   426	            if self._settlement_routed_at > 0 and elapsed > self._SETTLEMENT_COOLDOWN_S:
   427	                logger.info(
   428	                    "fr.settlement_confirmed_by_timeout symbols=%s elapsed=%.0fs",
   429	                    list(self._pending_settlement_positions.keys()), elapsed,
   430	                )
   431	                self._pending_settlement_positions.clear()
   432	                self._settlement_routed_at = 0.0
   433	            elif self._settlement_routed_at == 0.0:
   434	                logger.warning(
   435	                    "fr.settlement_positions_unconfirmed symbols=%s — "
   436	                    "no exit requests routed yet; use /closepositions if positions exist",
   437	                    list(self._pending_settlement_positions.keys()),
   438	                )
   439	        reqs = list(self._pending_exit_requests)
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '1188,1338p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1188	                    pass
  1189	            self._stats.trades_risk_blocked += 1
  1190	            self._notify_pre_exec_rollback(trade_request, sid)
  1191	            return
  1192	
  1193	        # --- Risk guardian check ---
  1194	        if self._risk_guardian is not None:
  1195	            try:
  1196	                approved = True
  1197	                if hasattr(self._risk_guardian, 'check_trade_request'):
  1198	                    approved = self._risk_guardian.check_trade_request(
  1199	                        trade_request, self._total_capital_usd
  1200	                    )
  1201	                elif hasattr(self._risk_guardian, 'approve'):
  1202	                    approved = self._risk_guardian.approve(trade_request)
  1203	                if not approved:
  1204	                    self._stats.trades_risk_blocked += 1
  1205	                    strat_stats.rejections += 1
  1206	                    logger.info("live_mode.risk_rejected strategy=%s", sid)
  1207	                    self._notify_pre_exec_rollback(trade_request, sid)
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '1335,1360p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  1335	            try:
  1336	                exec_result = await self._route_to_executor(trade_request, orders)
  1337	            except Exception as _exec_exc:
  1338	                # Unhandled executor exception: notify strategy to clear _open_positions
  1339	                # so a phantom position record doesn't block re-entry for 30min (BUG-MAJOR-15).
  1340	                logger.error(
  1341	                    "live_mode.executor_unhandled_error strategy=%s error=%s — notifying rollback",
  1342	                    sid, _exec_exc,
  1343	                )
  1344	                self._notify_pre_exec_rollback(trade_request, sid)
  1345	                return
  1346	
  1347	            if LIVE_EXECUTION_TIME is not None:
  1348	                LIVE_EXECUTION_TIME.observe(time.monotonic() - t0)
  1349	
  1350	            # --- Validate execution result ---
  1351	            if exec_result is not None and hasattr(exec_result, 'status'):
  1352	                from src.execution.executor import ExecutionStatus
  1353	                if exec_result.status != ExecutionStatus.SUCCESS:
  1354	                    logger.warning(
exec
/bin/zsh -lc "nl -ba src/strategies/funding_rate.py | sed -n '396,430p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   396	            expected_profit_usdt=net_profit,
   397	            confidence=signal.confidence,
   398	            metadata={
   399	                "funding_diff_bps": str(funding_diff_bps),
   400	                "max_holding_periods": str(self.config.max_holding_periods),
   401	                "expected_funding_income": str(expected_funding_income),
   402	                "total_cost": str(total_cost),
   403	            },
   404	        )
   405	
   406	    async def on_fill(self, trade: Trade) -> None:
   407	        await super().on_fill(trade)
   408	
   409	    def pop_exit_requests(self) -> list[TradeRequest]:
   410	        """Drain and return pending settlement-close TradeRequests.
   411	
   412	        Called by _strategy_exit_poll_loop in main.py every 60s so that
   413	        settlement exits are routed even when no new signal arrives.
   414	
   415	        Issue#4 fix: _pending_settlement_positions retains position data until
codex
**CRITICAL**
- Exit rollback semantics are still wrong in both pipelines. `live.py`’s pre-exec rollback helper always dispatches `handle_entry_rollback()` regardless of whether the request is an exit, and the post-exec non-success branch does the same for every `ROLLED_BACK`/`REJECTED` result. That breaks the WS-2 contract for reduce-only exits: FF should restore `_pending_exits -> _open_positions`, and FR should restore `_pending_settlement_positions -> _open_positions`, but neither happens on these paths. Result: a real exchange position can remain open while strategy tracking is not restored, so the monitor stops managing it. See [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1908), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1414), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:246), [funding_rate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/funding_rate.py:471). The same bug still exists in the `TradeRequestConsumer` callback path wired via [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:1566) and handled at [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:2093).

- `BUG-94` removes optimistic `_open_positions`, but `_pending_position_metadata` is not part of the per-symbol duplicate guard and has no expiry/reconciliation path. If execution succeeds on exchange but `on_execution_success()` is never reached, that dict entry leaks indefinitely, and the same symbol can still re-enter because `on_signal()` only blocks `_pending_entry_symbols` and `_open_positions`. The global slot count may still allow it. That is more than a memory leak; it can create duplicate live positions after a crash or callback miss. See [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:103), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:468), [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:653), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1450).

**HIGH**
- A ghost or stranded half-position is still possible in the exit-drain path because `live.py` checks only the first exit leg’s exchange before deciding to `clear_ghost()` and skip the exit. For a two-exchange hedge, if leg 1 is gone but leg 2 still exists, this path wipes all strategy tracking and leaves the remaining leg unmanaged. See [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:2314) and [futures_futures.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/futures_futures.py:273).

**MEDIUM**
- The WS-2 success-side refactor is not actually wired. `BaseStrategy` now defines `handle_entry_success()` / `handle_exit_success()`, but `live.py` still calls only legacy `on_execution_success()` and only for `legs[0].symbol`, while `main.py`’s consumer callback calls no success callback at all. For FF this mostly works because of the legacy method, but FR’s `handle_exit_success()` is dead and its pending settlement state clears only by timeout, not on confirmed success. See [base.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/base.py:143), [live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:1450), [funding_rate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/strategies/funding_rate.py:485), [main.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/main.py:1803).

**LOW**
- `_apply_engine_json_overrides()` is not complete beyond the three fields you added. The concrete current miss is `live_gate.continuous_enabled` from `engine.json`, which is not mapped into settings even though runtime reads `operational.live_gate_continuous_enabled`; changing it in `engine.json` has no effect. Other uncovered settings fields include `live_gate.min_exchange_health`, `live_gate.reevaluation_interval_hours`, `execution.rollback_timeout_ms`, `execution.reconciliation_interval_s`, and `risk.max_rollback_threshold`. Separately, `risk.circuit_breaker_mdd_threshold` is still not represented in `Settings` at all. See [engine.json](/Users/100aniv/Development/arbitrage_OMC/engine/config/engine.json:140), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:334), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:368), [config.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/core/config.py:625), [live_gate.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live_gate.py:115).
tokens used
100,315

```

## Concise summary

Provider completed successfully. Review the raw output for details.

## Action items

- Review the response and extract decisions you want to apply.
- Capture follow-up implementation tasks if needed.
