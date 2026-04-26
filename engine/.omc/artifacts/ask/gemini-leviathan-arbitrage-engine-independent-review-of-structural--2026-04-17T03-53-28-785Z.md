# gemini advisor artifact

- Provider: gemini
- Exit code: 0
- Created at: 2026-04-17T03:53:28.787Z

## Original task

LEVIATHAN arbitrage engine — Independent review of structural refactoring.

Commits under review: 3cfb65c, 1a5c80a, 80df207, cb0312d.
Key change (BUG-94): engine/src/strategies/futures_futures.py

Previously on_signal() wrote self._open_positions[sym] = {buy_ex, sell_ex, size, entry_time} BEFORE execution. This caused 11 ghost_cleared events in v123 canary when executions failed on various guards or partial fills.

Fix: New self._pending_position_metadata dict is written in on_signal. Promotion to _open_positions happens ONLY in on_execution_success (i.e., confirmed fill). handle_entry_rollback, clear_ghost, and handle_entry_success all correctly pop from _pending_position_metadata.

Your mandate:
1. Review the UX/readability of this refactor — is the code easy for another engineer to understand?
2. Edge cases: what if monitor loops iterate _pending_position_metadata? (Currently they only iterate _open_positions — is that safe?)
3. Alternative approaches — is there a cleaner pattern (event sourcing, state machine library, etc.) we should consider?
4. Documentation gaps — what needs to be documented that isn't?
5. Could this introduce new ghosts we haven't anticipated?

Consider the user's stated goal: institutional-grade reliability (0 ghost, sub-500ms latency, real-time MTM PnL). Currently we're at 'functional prototype'.

Return concise, actionable feedback.

## Final prompt

LEVIATHAN arbitrage engine — Independent review of structural refactoring.

Commits under review: 3cfb65c, 1a5c80a, 80df207, cb0312d.
Key change (BUG-94): engine/src/strategies/futures_futures.py

Previously on_signal() wrote self._open_positions[sym] = {buy_ex, sell_ex, size, entry_time} BEFORE execution. This caused 11 ghost_cleared events in v123 canary when executions failed on various guards or partial fills.

Fix: New self._pending_position_metadata dict is written in on_signal. Promotion to _open_positions happens ONLY in on_execution_success (i.e., confirmed fill). handle_entry_rollback, clear_ghost, and handle_entry_success all correctly pop from _pending_position_metadata.

Your mandate:
1. Review the UX/readability of this refactor — is the code easy for another engineer to understand?
2. Edge cases: what if monitor loops iterate _pending_position_metadata? (Currently they only iterate _open_positions — is that safe?)
3. Alternative approaches — is there a cleaner pattern (event sourcing, state machine library, etc.) we should consider?
4. Documentation gaps — what needs to be documented that isn't?
5. Could this introduce new ghosts we haven't anticipated?

Consider the user's stated goal: institutional-grade reliability (0 ghost, sub-500ms latency, real-time MTM PnL). Currently we're at 'functional prototype'.

Return concise, actionable feedback.

## Raw output

```text
I will start by examining the refactored code in `engine/src/strategies/futures_futures.py` and reviewing the related state management logic.
I will search for the callers of `handle_entry_rollback`, `handle_exit_rollback`, `handle_entry_success`, and `handle_exit_success` to understand the lifecycle of the `TradeRequest` and how these state transitions are triggered.
I will examine the `src/modes/live.py` file around line 1425 to understand which callbacks are being used and how the state transition is managed.
I will read the `src/modes/live.py` file starting from line 1451 to understand how successful executions are handled and how the strategy is notified.
I will search for the usage of `handle_` methods in `src/main.py` to see how it manages success and failure notifications and compare it with `live.py`.
I will re-examine `src/modes/live.py` around line 1380 to see if it distinguishes between entry and exit for `ROLLED_BACK` status and how it handles each case.
I will search for the string `on_execution_rollback` in `src/modes/live.py` to determine if and where this callback is invoked.
I will check the context around line 1420 in `src/modes/live.py` to see if there's any logic that distinguishes between entry and exit before calling `handle_entry_rollback`.
I will check the definition and usage of `_is_reduceonly_request` in `src/modes/live.py` to see how it identifies exit requests.
I will search for the definition of `_is_reduceonly_request` in `src/modes/live.py`.
I will search for `on_execution_rollback` in `src/main.py` to see if it's still being used in the main entry point.
I will read `src/modes/live.py` around the failure handling section to see if there's any other logic for `ROLLED_BACK` status that might handle exits correctly.
I will check `src/main.py` again to see if it also lacks the logic to distinguish between entry and exit rollbacks for the `ROLLED_BACK` status.
LEVIATHAN Arbitrage Engine — BUG-94 Structural Refactoring Review.

### 1. UX & Readability
The refactor is a significant improvement. The separation of `_pending_position_metadata` from `_open_positions` makes the "intent to trade" vs. "active exposure" state explicit. Using clear, searchable keys like `BUG-94` in comments helps future maintainers understand the rationale.

### 2. Edge Cases & Safety
*   **Monitor Loop Safety:** It is correct that `_open_positions_monitor` only iterates `_open_positions`. Exiting a position before its entry is confirmed (fill callback received) would risk "closing" a position that doesn't yet exist or is in an inconsistent state.
*   **Capacity Counting:** The updated `_cur_positions` logic (including pending entries, pending exits, and pending metadata) is **robust** and correctly prevents margin exhaustion (-2019) by accounting for all in-flight capital commitments.
*   **[CRITICAL] Exit Rollback Ghost:** Current `live.py` (line 1425) and `main.py` (line 2100) call `handle_entry_rollback` for any `ROLLED_BACK` status. Since `handle_entry_rollback` does not check `_pending_exits`, an exit that rolls back (e.g., partial fill unwind) will result in the position being dropped from tracking while remaining open on the exchange.
*   **[CRITICAL] Eager `on_fill` Cleanup:** The eager cleanup in `on_fill` (line 697) pops `_pending_exits` on the first fill. If a partial exit fill is unwound (rollback), the snapshot required to restore `_open_positions` is already gone.

### 3. Alternative Approaches
The strategy is reaching "state bloat" with 5+ parallel collections (`_open_positions`, `_pending_metadata`, `_pending_exits`, `_exiting_symbols`, `_pending_entry_symbols`).
*   **Recommendation:** Move to a unified `_positions: dict[str, Position]` where `Position` is a state-machine object (`PENDING_ENTRY` → `OPEN` → `PENDING_EXIT`). This eliminates the risk of state drift between dictionaries.

### 4. Documentation & Contract Gaps
*   **Callback Ambiguity:** The relationship between `on_execution_success` (used by `live.py`) and `handle_entry_success` (empty/unused) is confusing.
*   **Dead Code:** `on_execution_rollback` is still implemented in the strategy but no longer called by the engine (which now uses `handle_*_rollback`). This should be purged or consolidated to avoid confusion.

### 5. Actionable Fixes
1.  **Update Engine:** `live.py` and `main.py` must check `self._is_reduceonly_request(trade_request)` before calling `handle_entry_rollback`. If `True`, call `handle_exit_rollback`.
2.  **Defer `on_fill` Cleanup:** Only pop `_pending_exits` in `on_execution_success` or `handle_exit_success`. If eager cleanup is required for partial fills, it must be non-destructive to the rollback snapshot.
3.  **Consolidate Callbacks:** Route all rollback logic through the `handle_*` methods and deprecate the unified `on_execution_rollback`.


YOLO mode is enabled. All tool calls will be automatically approved.
Loaded cached credentials.
YOLO mode is enabled. All tool calls will be automatically approved.

```

## Concise summary

Provider completed successfully. Review the raw output for details.

## Action items

- Review the response and extract decisions you want to apply.
- Capture follow-up implementation tasks if needed.
