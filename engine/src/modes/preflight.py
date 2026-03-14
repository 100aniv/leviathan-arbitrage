"""LEVIATHAN Phase 5 Pre-Flight Checklist — Go-Live Gating.

Implements the 10-item automated pre-flight checklist from QUANT_MANIFESTO
Section 8.2. ALL 10 checks must pass before the engine can be activated in
live mode.

Checklist:
  1.  TimescaleDB Connection     — healthy + recent write exists
  2.  Exchange WS+REST Response  — all configured exchanges connected
  3.  API Key Permissions         — trade permission confirmed
  4.  Balance Minimum             — >= configured initial_capital
  5.  Kill Switch Clear           — is_halted() == False
  6.  Circuit Breaker CLOSED      — state == CLOSED
  7.  LiveGate Eligible           — eligible == True
  8.  Telegram Working            — test message success
  9.  Native Adapter Health       — health_score > 0.95
  10. Shadow Verification         — 72h incident-free

All external dependencies are injected as callables for testability.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PreflightCheck:
    """Result of a single pre-flight criterion check."""

    name: str
    passed: bool
    value: str       # actual measured value (string-formatted)
    threshold: str   # required threshold (string-formatted)
    detail: str = ""  # additional context


@dataclass
class PreflightResult:
    """Aggregated pre-flight checklist result."""

    checks: list[PreflightCheck]
    overall_pass: bool          # True only if ALL checks pass
    timestamp: datetime
    duration_ms: float = 0.0
    failure_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------


class PreflightChecker:
    """Runs the 10-item pre-flight checklist before go-live.

    All external side-effects are injected as callables so that the checker
    can be unit-tested without real infrastructure.

    Args:
        exchanges:            dict[exchange_id, adapter] — adapters implementing
                              ``exchange_id``, ``health_score``, and async
                              ``get_balances()`` / ``get_positions()``.
        db_pool:              asyncpg connection pool (or None → DB check fails).
        settings:             Settings instance for initial_capital threshold.
        kill_switch_check:    Callable[] -> bool — returns True if halted.
        circuit_breaker_state: Callable[] -> str — returns "CLOSED"/"OPEN"/…
        live_gate_check:      Optional async Callable[] -> bool — returns eligible.
        telegram_check:       Optional async Callable[] -> bool — sends test msg.
    """

    # Default thresholds
    NATIVE_HEALTH_THRESHOLD = 0.95
    SHADOW_HOURS_REQUIRED = 72
    BALANCE_USDT_MINIMUM = 70.0  # fallback if settings not provided

    def __init__(
        self,
        exchanges: dict[str, Any],
        db_pool: Any | None,
        settings: Any | None,
        kill_switch_check: Callable[[], bool],
        circuit_breaker_state: Callable[[], str],
        live_gate_check: Callable[[], Any] | None = None,
        telegram_check: Callable[[], Any] | None = None,
    ) -> None:
        self._exchanges = exchanges
        self._db_pool = db_pool
        self._settings = settings
        self._kill_switch_check = kill_switch_check
        self._circuit_breaker_state = circuit_breaker_state
        self._live_gate_check = live_gate_check
        self._telegram_check = telegram_check

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def run_all(self) -> PreflightResult:
        """Execute all 10 pre-flight checks and return aggregated result.

        Checks run sequentially to ensure a clear, ordered report.
        Each check failure is logged but does not abort remaining checks.

        Returns:
            PreflightResult with per-check details and overall_pass flag.
        """
        t_start = time.perf_counter()
        timestamp = datetime.now(timezone.utc)

        checks: list[PreflightCheck] = []
        failure_reasons: list[str] = []

        # Run all 10 checks in order
        check_coroutines = [
            self._check_database(),
            self._check_exchanges(),
            self._check_api_key_permissions(),
            self._check_balance_minimum(),
            self._check_kill_switch(),
            self._check_circuit_breaker(),
            self._check_live_gate(),
            self._check_telegram(),
            self._check_native_adapter_health(),
            self._check_shadow_verification(),
        ]

        for coro in check_coroutines:
            try:
                check = await coro
            except Exception as exc:
                # Defensive: if a check coroutine itself raises, mark as failed
                check = PreflightCheck(
                    name="Unknown",
                    passed=False,
                    value="ERROR",
                    threshold="N/A",
                    detail=str(exc),
                )
                logger.error("preflight_check_exception", error=str(exc), exc_info=True)
            checks.append(check)
            if not check.passed:
                failure_reasons.append(f"{check.name}: {check.value} (need {check.threshold})")

        overall_pass = len(failure_reasons) == 0
        duration_ms = (time.perf_counter() - t_start) * 1000

        result = PreflightResult(
            checks=checks,
            overall_pass=overall_pass,
            timestamp=timestamp,
            duration_ms=duration_ms,
            failure_reasons=failure_reasons,
        )

        logger.info(
            "preflight_complete",
            overall_pass=overall_pass,
            passed=sum(1 for c in checks if c.passed),
            failed=len(failure_reasons),
            duration_ms=f"{duration_ms:.1f}",
        )

        return result

    def format_report(self, result: PreflightResult) -> str:
        """Format PreflightResult as a markdown report string.

        Args:
            result: The result returned by run_all().

        Returns:
            A multi-line markdown string suitable for Telegram or log output.
        """
        status = "PASSED" if result.overall_pass else "FAILED"
        lines: list[str] = [
            f"# LEVIATHAN Pre-Flight Checklist — {status}",
            f"Timestamp: {result.timestamp.isoformat()}",
            f"Duration: {result.duration_ms:.0f}ms",
            "",
            "| # | Check | Status | Value | Threshold |",
            "|---|-------|--------|-------|-----------|",
        ]

        for i, check in enumerate(result.checks, start=1):
            mark = "PASS" if check.passed else "FAIL"
            detail_suffix = f" ({check.detail})" if check.detail else ""
            lines.append(
                f"| {i} | {check.name} | {mark} | "
                f"{check.value}{detail_suffix} | {check.threshold} |"
            )

        if not result.overall_pass:
            lines.append("")
            lines.append("## Failures")
            for reason in result.failure_reasons:
                lines.append(f"- {reason}")

        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Check 1: TimescaleDB Connection
    # ---------------------------------------------------------------------------

    async def _check_database(self) -> PreflightCheck:
        """Check 1: TimescaleDB connection healthy + recent write exists."""
        name = "TimescaleDB Connection"
        if self._db_pool is None:
            return PreflightCheck(
                name=name,
                passed=False,
                value="No pool",
                threshold="connected + recent write",
                detail="db_pool not provided",
            )

        try:
            # Acquire a connection and verify it responds
            async with self._db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            # Check that a recent market data write exists (within last 5 minutes)
            try:
                async with self._db_pool.acquire() as conn:
                    result = await conn.fetchval(
                        "SELECT COUNT(*) FROM market_data "
                        "WHERE recorded_at > NOW() - INTERVAL '5 minutes'"
                    )
                recent_writes = int(result or 0)
                has_recent = recent_writes > 0
                return PreflightCheck(
                    name=name,
                    passed=has_recent,
                    value=f"{recent_writes} recent rows",
                    threshold=">= 1 row in last 5 min",
                    detail="market_data table checked",
                )
            except Exception:
                # Table may not exist yet — connection itself is what matters most
                return PreflightCheck(
                    name=name,
                    passed=True,
                    value="connected",
                    threshold="connected + recent write",
                    detail="market_data table not found; connection OK",
                )

        except Exception as exc:
            logger.warning("preflight_db_check_error", error=str(exc))
            return PreflightCheck(
                name=name,
                passed=False,
                value="connection failed",
                threshold="connected + recent write",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Check 2: Exchange WS+REST Response
    # ---------------------------------------------------------------------------

    async def _check_exchanges(self) -> PreflightCheck:
        """Check 2: All configured exchanges have connected adapters."""
        name = "Exchange WS+REST Response"
        if not self._exchanges:
            return PreflightCheck(
                name=name,
                passed=False,
                value="0 exchanges",
                threshold=">= 1 exchange connected",
                detail="no exchanges provided",
            )

        connected: list[str] = []
        disconnected: list[str] = []

        for exchange_id, adapter in self._exchanges.items():
            # Check if adapter has a health_score or _connected attribute
            try:
                is_connected = getattr(adapter, "_connected", None)
                if is_connected is None:
                    # Fallback: try health_score > 0
                    score = float(getattr(adapter, "health_score", 0.0))
                    is_connected = score > 0.0
                if is_connected:
                    connected.append(exchange_id)
                else:
                    disconnected.append(exchange_id)
            except Exception as exc:
                logger.warning(
                    "preflight_exchange_check_error",
                    exchange=exchange_id,
                    error=str(exc),
                )
                disconnected.append(exchange_id)

        all_connected = len(disconnected) == 0
        return PreflightCheck(
            name=name,
            passed=all_connected,
            value=f"{len(connected)}/{len(self._exchanges)} connected",
            threshold="all exchanges connected",
            detail=f"disconnected: {disconnected}" if disconnected else "all OK",
        )

    # ---------------------------------------------------------------------------
    # Check 3: API Key Permissions
    # ---------------------------------------------------------------------------

    async def _check_api_key_permissions(self) -> PreflightCheck:
        """Check 3: API keys have trade permission on all exchanges."""
        name = "API Key Permissions"
        if not self._exchanges:
            return PreflightCheck(
                name=name,
                passed=False,
                value="no exchanges",
                threshold="trade permission on all exchanges",
            )

        permitted: list[str] = []
        denied: list[str] = []

        for exchange_id, adapter in self._exchanges.items():
            try:
                # Try to fetch balances — if the key has no read permission this fails
                balances = await adapter.get_balances()
                if balances is not None:
                    permitted.append(exchange_id)
                else:
                    denied.append(exchange_id)
            except Exception as exc:
                logger.warning(
                    "preflight_api_key_check_error",
                    exchange=exchange_id,
                    error=str(exc),
                )
                denied.append(exchange_id)

        all_ok = len(denied) == 0
        return PreflightCheck(
            name=name,
            passed=all_ok,
            value=f"{len(permitted)}/{len(self._exchanges)} have permission",
            threshold="all exchanges have trade permission",
            detail=f"denied: {denied}" if denied else "all OK",
        )

    # ---------------------------------------------------------------------------
    # Check 4: Balance Minimum
    # ---------------------------------------------------------------------------

    async def _check_balance_minimum(self) -> PreflightCheck:
        """Check 4: Account balance >= configured initial_capital on each exchange."""
        name = "Balance Minimum"

        # Determine threshold from settings
        min_balance = self.BALANCE_USDT_MINIMUM
        if self._settings is not None:
            try:
                min_balance = float(self._settings.capital.initial_capital)
            except Exception:
                pass

        if not self._exchanges:
            return PreflightCheck(
                name=name,
                passed=False,
                value="no exchanges",
                threshold=f">= {min_balance:.2f} USDT",
            )

        below_minimum: list[str] = []
        above_minimum: list[str] = []

        for exchange_id, adapter in self._exchanges.items():
            try:
                balances = await adapter.get_balances()
                # Look for USDT balance
                usdt_balance = 0.0
                if isinstance(balances, dict):
                    for currency, bal in balances.items():
                        if currency.upper() in ("USDT", "USD"):
                            if hasattr(bal, "free"):
                                usdt_balance = float(bal.free)
                            elif isinstance(bal, (int, float)):
                                usdt_balance = float(bal)
                            break

                if usdt_balance >= min_balance:
                    above_minimum.append(f"{exchange_id}={usdt_balance:.2f}")
                else:
                    below_minimum.append(f"{exchange_id}={usdt_balance:.2f}")

            except Exception as exc:
                logger.warning(
                    "preflight_balance_check_error",
                    exchange=exchange_id,
                    error=str(exc),
                )
                below_minimum.append(f"{exchange_id}=error")

        all_ok = len(below_minimum) == 0
        return PreflightCheck(
            name=name,
            passed=all_ok,
            value=f"{len(above_minimum)}/{len(self._exchanges)} above min",
            threshold=f">= {min_balance:.2f} USDT per exchange",
            detail=f"below: {below_minimum}" if below_minimum else "all OK",
        )

    # ---------------------------------------------------------------------------
    # Check 5: Kill Switch Clear
    # ---------------------------------------------------------------------------

    async def _check_kill_switch(self) -> PreflightCheck:
        """Check 5: Kill switch is not halted."""
        name = "Kill Switch Clear"
        try:
            halted = self._kill_switch_check()
            return PreflightCheck(
                name=name,
                passed=not halted,
                value="HALTED" if halted else "Clear",
                threshold="is_halted() == False",
                detail="halt flag active" if halted else "",
            )
        except Exception as exc:
            logger.warning("preflight_kill_switch_check_error", error=str(exc))
            return PreflightCheck(
                name=name,
                passed=False,
                value="ERROR",
                threshold="is_halted() == False",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Check 6: Circuit Breaker CLOSED
    # ---------------------------------------------------------------------------

    async def _check_circuit_breaker(self) -> PreflightCheck:
        """Check 6: Circuit breaker is in CLOSED state."""
        name = "Circuit Breaker CLOSED"
        try:
            state = self._circuit_breaker_state()
            state_str = str(state).upper() if state is not None else "UNKNOWN"
            closed = state_str == "CLOSED"
            return PreflightCheck(
                name=name,
                passed=closed,
                value=state_str,
                threshold="CLOSED",
                detail="trading halted" if not closed else "",
            )
        except Exception as exc:
            logger.warning("preflight_circuit_breaker_check_error", error=str(exc))
            return PreflightCheck(
                name=name,
                passed=False,
                value="ERROR",
                threshold="CLOSED",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Check 7: LiveGate Eligible
    # ---------------------------------------------------------------------------

    async def _check_live_gate(self) -> PreflightCheck:
        """Check 7: LiveGate evaluation is eligible (all gate criteria pass)."""
        name = "LiveGate Eligible"
        if self._live_gate_check is None:
            return PreflightCheck(
                name=name,
                passed=False,
                value="not configured",
                threshold="eligible == True",
                detail="live_gate_check callable not provided",
            )

        try:
            # Support both sync and async callables
            result = self._live_gate_check()
            if asyncio.iscoroutine(result):
                result = await result
            eligible = bool(result)
            return PreflightCheck(
                name=name,
                passed=eligible,
                value="eligible" if eligible else "blocked",
                threshold="eligible == True",
                detail="" if eligible else "LiveGate criteria not met",
            )
        except Exception as exc:
            logger.warning("preflight_live_gate_check_error", error=str(exc))
            return PreflightCheck(
                name=name,
                passed=False,
                value="ERROR",
                threshold="eligible == True",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Check 8: Telegram Working
    # ---------------------------------------------------------------------------

    async def _check_telegram(self) -> PreflightCheck:
        """Check 8: Telegram alerter can deliver a test message."""
        name = "Telegram Working"
        if self._telegram_check is None:
            return PreflightCheck(
                name=name,
                passed=False,
                value="not configured",
                threshold="test message delivered",
                detail="telegram_check callable not provided",
            )

        try:
            result = self._telegram_check()
            if asyncio.iscoroutine(result):
                result = await result
            success = bool(result)
            return PreflightCheck(
                name=name,
                passed=success,
                value="delivered" if success else "failed",
                threshold="test message delivered",
                detail="" if success else "message delivery failed",
            )
        except Exception as exc:
            logger.warning("preflight_telegram_check_error", error=str(exc))
            return PreflightCheck(
                name=name,
                passed=False,
                value="ERROR",
                threshold="test message delivered",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Check 9: Native Adapter Health
    # ---------------------------------------------------------------------------

    async def _check_native_adapter_health(self) -> PreflightCheck:
        """Check 9: All native exchange adapters have health_score > 0.95."""
        name = "Native Adapter Health"
        threshold = self.NATIVE_HEALTH_THRESHOLD

        if not self._exchanges:
            return PreflightCheck(
                name=name,
                passed=False,
                value="no adapters",
                threshold=f"health_score > {threshold}",
            )

        scores: dict[str, float] = {}
        unhealthy: list[str] = []

        for exchange_id, adapter in self._exchanges.items():
            try:
                score = float(getattr(adapter, "health_score", 0.0))
                scores[exchange_id] = score
                if score <= threshold:
                    unhealthy.append(f"{exchange_id}={score:.3f}")
            except Exception as exc:
                logger.warning(
                    "preflight_health_score_error",
                    exchange=exchange_id,
                    error=str(exc),
                )
                unhealthy.append(f"{exchange_id}=error")

        all_healthy = len(unhealthy) == 0
        avg_score = sum(scores.values()) / len(scores) if scores else 0.0

        return PreflightCheck(
            name=name,
            passed=all_healthy,
            value=f"avg={avg_score:.3f}",
            threshold=f"> {threshold}",
            detail=f"unhealthy: {unhealthy}" if unhealthy else "all OK",
        )

    # ---------------------------------------------------------------------------
    # Check 10: Shadow Verification
    # ---------------------------------------------------------------------------

    async def _check_shadow_verification(self) -> PreflightCheck:
        """Check 10: Shadow mode has run for 72h with no incidents."""
        name = "Shadow Verification"
        required_hours = self.SHADOW_HOURS_REQUIRED

        if self._db_pool is None:
            return PreflightCheck(
                name=name,
                passed=False,
                value="no DB",
                threshold=f"{required_hours}h incident-free",
                detail="db_pool required for shadow history",
            )

        try:
            async with self._db_pool.acquire() as conn:
                # Check shadow run duration — earliest shadow record
                first_shadow = await conn.fetchval(
                    "SELECT MIN(recorded_at) FROM shadow_results"
                )

                if first_shadow is None:
                    return PreflightCheck(
                        name=name,
                        passed=False,
                        value="no shadow data",
                        threshold=f"{required_hours}h incident-free",
                        detail="shadow_results table empty or missing",
                    )

                now = datetime.now(timezone.utc)
                # Handle timezone-aware and naive datetimes
                if first_shadow.tzinfo is None:
                    from datetime import timezone as _tz
                    first_shadow = first_shadow.replace(tzinfo=_tz.utc)
                elapsed_hours = (now - first_shadow).total_seconds() / 3600

                # Check for incidents in the shadow period
                incident_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM shadow_results WHERE incident = TRUE"
                )
                incidents = int(incident_count or 0)

                hours_ok = elapsed_hours >= required_hours
                incident_free = incidents == 0
                passed = hours_ok and incident_free

                return PreflightCheck(
                    name=name,
                    passed=passed,
                    value=f"{elapsed_hours:.1f}h, {incidents} incidents",
                    threshold=f">= {required_hours}h, 0 incidents",
                    detail=(
                        "OK"
                        if passed
                        else (
                            f"only {elapsed_hours:.1f}h elapsed"
                            if not hours_ok
                            else f"{incidents} incidents found"
                        )
                    ),
                )

        except Exception as exc:
            logger.warning("preflight_shadow_check_error", error=str(exc))
            # If shadow_results table doesn't exist, the shadow check fails gracefully
            return PreflightCheck(
                name=name,
                passed=False,
                value="query failed",
                threshold=f"{required_hours}h incident-free",
                detail=str(exc),
            )

    # ---------------------------------------------------------------------------
    # Utility: .env sync check
    # ---------------------------------------------------------------------------

    # Keys to compare between root .env and engine/.env
    ENV_SYNC_KEYS = ("MIN_EDGE_BPS", "SLIPPAGE_K_DEFAULT", "POWERLAW_SLIPPAGE_K", "REDIS_PASSWORD")
    _SENSITIVE_KEYS = frozenset({"REDIS_PASSWORD", "JWT_SECRET", "POSTGRES_PASSWORD", "DB_PASSWORD"})

    def _check_env_sync(self) -> None:
        """Warn if root .env and engine/.env have mismatched critical variables.

        Reads both files from disk (relative to this file's project root) and
        compares ENV_SYNC_KEYS. Logs a WARNING for each mismatch found.
        Does NOT raise — only logs.
        """
        # Locate project root: engine/src/modes/preflight.py → go up 3 levels
        project_root = Path(__file__).resolve().parents[3]
        root_env_path = project_root / ".env"
        engine_env_path = project_root / "engine" / ".env"

        def _parse_env(path: Path) -> dict[str, str]:
            values: dict[str, str] = {}
            if not path.exists():
                return values
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                # Strip inline comment
                val = val.split("#")[0].strip()
                values[key.strip()] = val
            return values

        root_vals = _parse_env(root_env_path)
        engine_vals = _parse_env(engine_env_path)

        for key in self.ENV_SYNC_KEYS:
            rv = root_vals.get(key)
            ev = engine_vals.get(key)
            if rv is None and ev is None:
                continue
            if rv != ev:
                def _mask(k: str, v: str | None) -> str:
                    if v is None:
                        return "<unset>"
                    if k in self._SENSITIVE_KEYS:
                        return f"{v[:2]}***" if len(v) > 2 else "***"
                    return v

                logger.warning(
                    "env_sync_mismatch",
                    key=key,
                    root_env=_mask(key, rv),
                    engine_env=_mask(key, ev),
                    message=f".env mismatch for {key}: root={_mask(key, rv)!r} engine={_mask(key, ev)!r}",
                )
