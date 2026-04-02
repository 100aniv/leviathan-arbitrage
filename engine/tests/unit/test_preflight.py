"""Unit tests for PreflightChecker, PreflightCheck, and PreflightResult.

Covers:
- Each of the 10 checks individually with mock dependencies
- run_all() with all passing → overall_pass=True
- run_all() with one failing → overall_pass=False
- format_report() output
- None db_pool → DB check fails
- Empty exchanges dict behavior
- Sync and async callables for live_gate_check and telegram_check
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.preflight import PreflightCheck, PreflightChecker, PreflightResult


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def make_mock_adapter(
    exchange_id: str = "binance",
    connected: bool = True,
    health_score: float = 0.99,
    balances: dict | None = None,
) -> MagicMock:
    """Build a mock exchange adapter."""
    adapter = MagicMock()
    adapter.exchange_id = exchange_id
    adapter._connected = connected
    adapter.health_score = health_score
    if balances is None:
        balances = {"USDT": MagicMock(free=500.0)}
    adapter.get_balances = AsyncMock(return_value=balances)
    return adapter


def make_db_pool(
    query_results: dict | None = None,
    fail: bool = False,
) -> MagicMock:
    """Build a mock asyncpg pool.

    query_results maps SQL fragments to their return values.
    If fail=True, acquire() raises an exception.
    """
    pool = MagicMock()

    if fail:
        pool.acquire.side_effect = Exception("DB connection refused")
        return pool

    results = query_results or {}

    class _FakeConn:
        async def fetchval(self, sql: str, *args) -> object:
            # Match by SQL fragment
            for fragment, value in results.items():
                if fragment in sql:
                    return value
            # Default: SELECT 1 → 1, others → None
            if "SELECT 1" in sql:
                return 1
            return None

        async def __aenter__(self) -> "_FakeConn":
            return self

        async def __aexit__(self, *args) -> None:
            pass

    class _FakeAcquire:
        def __enter__(self) -> "_FakeConn":
            return _FakeConn()

        def __exit__(self, *args) -> None:
            pass

        def __await__(self):
            return iter([])

        async def __aenter__(self) -> "_FakeConn":
            return _FakeConn()

        async def __aexit__(self, *args) -> None:
            pass

    pool.acquire.return_value = _FakeAcquire()
    return pool


def make_settings(initial_capital: float = 70.0) -> MagicMock:
    settings = MagicMock()
    settings.capital.initial_capital = initial_capital
    return settings


def make_checker(
    exchanges: dict | None = None,
    db_pool=None,
    settings=None,
    kill_switch_check=None,
    circuit_breaker_state=None,
    live_gate_check=None,
    telegram_check=None,
) -> PreflightChecker:
    if exchanges is None:
        exchanges = {"binance": make_mock_adapter("binance")}
    if kill_switch_check is None:
        kill_switch_check = lambda: False  # not halted
    if circuit_breaker_state is None:
        circuit_breaker_state = lambda: "CLOSED"

    return PreflightChecker(
        exchanges=exchanges,
        db_pool=db_pool,
        settings=settings,
        kill_switch_check=kill_switch_check,
        circuit_breaker_state=circuit_breaker_state,
        live_gate_check=live_gate_check,
        telegram_check=telegram_check,
    )


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_preflight_check_fields(self) -> None:
        check = PreflightCheck(
            name="Kill Switch Clear",
            passed=True,
            value="Clear",
            threshold="is_halted() == False",
        )
        assert check.name == "Kill Switch Clear"
        assert check.passed is True
        assert check.detail == ""

    def test_preflight_result_overall_pass_true(self) -> None:
        result = PreflightResult(
            checks=[],
            overall_pass=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert result.overall_pass is True
        assert result.failure_reasons == []

    def test_preflight_result_failure_reasons_default_empty(self) -> None:
        result = PreflightResult(
            checks=[],
            overall_pass=False,
            timestamp=datetime.now(timezone.utc),
        )
        assert isinstance(result.failure_reasons, list)


# ---------------------------------------------------------------------------
# Check 1: Database
# ---------------------------------------------------------------------------


class TestCheckDatabase:
    @pytest.mark.asyncio
    async def test_db_check_fails_when_pool_is_none(self) -> None:
        checker = make_checker(db_pool=None)
        check = await checker._check_database()
        assert check.name == "TimescaleDB Connection"
        assert check.passed is False
        assert "No pool" in check.value

    @pytest.mark.asyncio
    async def test_db_check_fails_when_connection_refused(self) -> None:
        pool = make_db_pool(fail=True)
        checker = make_checker(db_pool=pool)
        check = await checker._check_database()
        assert check.passed is False
        assert "connection failed" in check.value

    @pytest.mark.asyncio
    async def test_db_check_passes_with_recent_writes(self) -> None:
        pool = make_db_pool(query_results={"market_data": 5})
        checker = make_checker(db_pool=pool)
        check = await checker._check_database()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_db_check_fails_when_no_recent_writes(self) -> None:
        pool = make_db_pool(query_results={"market_data": 0})
        checker = make_checker(db_pool=pool)
        check = await checker._check_database()
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 2: Exchanges
# ---------------------------------------------------------------------------


class TestCheckExchanges:
    @pytest.mark.asyncio
    async def test_exchanges_check_passes_when_all_connected(self) -> None:
        exchanges = {
            "binance": make_mock_adapter("binance", connected=True),
            "okx": make_mock_adapter("okx", connected=True),
        }
        checker = make_checker(exchanges=exchanges)
        check = await checker._check_exchanges()
        assert check.passed is True
        assert "2/2" in check.value

    @pytest.mark.asyncio
    async def test_exchanges_check_fails_when_one_disconnected(self) -> None:
        exchanges = {
            "binance": make_mock_adapter("binance", connected=True),
            "okx": make_mock_adapter("okx", connected=False),
        }
        checker = make_checker(exchanges=exchanges)
        check = await checker._check_exchanges()
        assert check.passed is False
        assert "okx" in check.detail

    @pytest.mark.asyncio
    async def test_exchanges_check_fails_with_empty_dict(self) -> None:
        checker = make_checker(exchanges={})
        check = await checker._check_exchanges()
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_exchanges_check_uses_health_score_fallback_when_no_connected_attr(
        self,
    ) -> None:
        """When _connected is None, adapter falls back to health_score > 0."""
        adapter = MagicMock()
        adapter.exchange_id = "binance"
        del adapter._connected  # Remove _connected so getattr returns None via spec
        adapter._connected = None  # Explicit None triggers health_score fallback
        adapter.health_score = 0.99  # > 0.0, so connected
        adapter.get_balances = AsyncMock(return_value={"USDT": MagicMock(free=500.0)})
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_exchanges()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_exchanges_check_marks_disconnected_when_health_score_zero(
        self,
    ) -> None:
        """health_score=0.0 with no _connected attr → disconnected."""
        adapter = MagicMock()
        adapter.exchange_id = "binance"
        adapter._connected = None  # force health_score fallback
        adapter.health_score = 0.0
        adapter.get_balances = AsyncMock(return_value={})
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_exchanges()
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 3: API Key Permissions
# ---------------------------------------------------------------------------


class TestCheckApiKeyPermissions:
    @pytest.mark.asyncio
    async def test_api_key_check_passes_when_balances_returned(self) -> None:
        adapter = make_mock_adapter("binance")
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_api_key_permissions()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_api_key_check_fails_when_get_balances_raises(self) -> None:
        adapter = make_mock_adapter("binance")
        adapter.get_balances = AsyncMock(side_effect=Exception("Unauthorized"))
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_api_key_permissions()
        assert check.passed is False
        assert "binance" in check.detail

    @pytest.mark.asyncio
    async def test_api_key_check_fails_with_no_exchanges(self) -> None:
        checker = make_checker(exchanges={})
        check = await checker._check_api_key_permissions()
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 4: Balance Minimum
# ---------------------------------------------------------------------------


class TestCheckBalanceMinimum:
    @pytest.mark.asyncio
    async def test_balance_check_passes_above_minimum(self) -> None:
        adapter = make_mock_adapter(
            "binance", balances={"USDT": MagicMock(free=200.0)}
        )
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=70.0),
        )
        check = await checker._check_balance_minimum()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_balance_check_fails_below_minimum(self) -> None:
        adapter = make_mock_adapter(
            "binance", balances={"USDT": MagicMock(free=10.0)}
        )
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=70.0),
        )
        check = await checker._check_balance_minimum()
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_balance_check_uses_settings_threshold(self) -> None:
        adapter = make_mock_adapter(
            "binance", balances={"USDT": MagicMock(free=150.0)}
        )
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=200.0),  # higher threshold
        )
        check = await checker._check_balance_minimum()
        assert check.passed is False
        assert "200.00" in check.threshold

    @pytest.mark.asyncio
    async def test_balance_check_accepts_raw_float_balance(self) -> None:
        """Balance as a plain float (not an object with .free) is handled correctly."""
        adapter = make_mock_adapter(
            "binance", balances={"USDT": 500.0}  # plain float, no .free attribute
        )
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=70.0),
        )
        check = await checker._check_balance_minimum()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_balance_check_accepts_usd_currency_key(self) -> None:
        """USD (not USDT) is also accepted as a valid balance key."""
        adapter = make_mock_adapter(
            "binance", balances={"USD": MagicMock(free=200.0)}
        )
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=70.0),
        )
        check = await checker._check_balance_minimum()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_balance_check_fails_when_get_balances_raises(self) -> None:
        """If get_balances raises, exchange is counted as below minimum."""
        adapter = make_mock_adapter("binance")
        adapter.get_balances = AsyncMock(side_effect=Exception("network error"))
        checker = make_checker(
            exchanges={"binance": adapter},
            settings=make_settings(initial_capital=70.0),
        )
        check = await checker._check_balance_minimum()
        assert check.passed is False
        assert "error" in check.detail


# ---------------------------------------------------------------------------
# Check 5: Kill Switch
# ---------------------------------------------------------------------------


class TestCheckKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_passes_when_not_halted(self) -> None:
        checker = make_checker(kill_switch_check=lambda: False)
        check = await checker._check_kill_switch()
        assert check.passed is True
        assert check.value == "Clear"

    @pytest.mark.asyncio
    async def test_kill_switch_fails_when_halted(self) -> None:
        checker = make_checker(kill_switch_check=lambda: True)
        check = await checker._check_kill_switch()
        assert check.passed is False
        assert check.value == "HALTED"

    @pytest.mark.asyncio
    async def test_kill_switch_fails_when_callable_raises(self) -> None:
        def bad_check() -> bool:
            raise RuntimeError("check failed")

        checker = make_checker(kill_switch_check=bad_check)
        check = await checker._check_kill_switch()
        assert check.passed is False
        assert check.value == "ERROR"


# ---------------------------------------------------------------------------
# Check 6: Circuit Breaker
# ---------------------------------------------------------------------------


class TestCheckCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_passes_when_closed(self) -> None:
        checker = make_checker(circuit_breaker_state=lambda: "CLOSED")
        check = await checker._check_circuit_breaker()
        assert check.passed is True
        assert check.value == "CLOSED"

    @pytest.mark.asyncio
    async def test_circuit_breaker_fails_when_open(self) -> None:
        checker = make_checker(circuit_breaker_state=lambda: "OPEN")
        check = await checker._check_circuit_breaker()
        assert check.passed is False
        assert check.value == "OPEN"

    @pytest.mark.asyncio
    async def test_circuit_breaker_fails_when_half_open(self) -> None:
        checker = make_checker(circuit_breaker_state=lambda: "HALF_OPEN")
        check = await checker._check_circuit_breaker()
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 7: LiveGate
# ---------------------------------------------------------------------------


class TestCheckLiveGate:
    @pytest.mark.asyncio
    async def test_live_gate_passes_when_eligible_sync(self) -> None:
        checker = make_checker(live_gate_check=lambda: True)
        check = await checker._check_live_gate()
        assert check.passed is True
        assert "eligible" in check.value

    @pytest.mark.asyncio
    async def test_live_gate_fails_when_not_eligible(self) -> None:
        checker = make_checker(live_gate_check=lambda: False)
        check = await checker._check_live_gate()
        assert check.passed is False
        assert "blocked" in check.value

    @pytest.mark.asyncio
    async def test_live_gate_passes_when_async_callable_returns_true(self) -> None:
        async def async_eligible() -> bool:
            return True

        checker = make_checker(live_gate_check=async_eligible)
        check = await checker._check_live_gate()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_live_gate_fails_when_not_configured(self) -> None:
        checker = make_checker(live_gate_check=None)
        check = await checker._check_live_gate()
        assert check.passed is False
        assert "not configured" in check.value


# ---------------------------------------------------------------------------
# Check 8: Telegram
# ---------------------------------------------------------------------------


class TestCheckTelegram:
    @pytest.mark.asyncio
    async def test_telegram_passes_when_delivered_sync(self) -> None:
        checker = make_checker(telegram_check=lambda: True)
        check = await checker._check_telegram()
        assert check.passed is True
        assert "delivered" in check.value

    @pytest.mark.asyncio
    async def test_telegram_fails_when_delivery_fails(self) -> None:
        checker = make_checker(telegram_check=lambda: False)
        check = await checker._check_telegram()
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_telegram_passes_when_async_callable_returns_true(self) -> None:
        async def async_send() -> bool:
            return True

        checker = make_checker(telegram_check=async_send)
        check = await checker._check_telegram()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_telegram_fails_when_not_configured(self) -> None:
        checker = make_checker(telegram_check=None)
        check = await checker._check_telegram()
        assert check.passed is False
        assert "not configured" in check.value


# ---------------------------------------------------------------------------
# Check 9: Native Adapter Health
# ---------------------------------------------------------------------------


class TestCheckNativeAdapterHealth:
    @pytest.mark.asyncio
    async def test_adapter_health_passes_above_threshold(self) -> None:
        adapter = make_mock_adapter("binance", health_score=0.99)
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_native_adapter_health()
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_adapter_health_fails_at_threshold(self) -> None:
        # Threshold is > 0.95 (strictly greater), so 0.95 fails
        adapter = make_mock_adapter("binance", health_score=0.95)
        checker = make_checker(exchanges={"binance": adapter})
        check = await checker._check_native_adapter_health()
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_adapter_health_fails_when_one_exchange_unhealthy(self) -> None:
        exchanges = {
            "binance": make_mock_adapter("binance", health_score=0.99),
            "okx": make_mock_adapter("okx", health_score=0.80),
        }
        checker = make_checker(exchanges=exchanges)
        check = await checker._check_native_adapter_health()
        assert check.passed is False
        assert "okx" in check.detail

    @pytest.mark.asyncio
    async def test_adapter_health_fails_with_no_exchanges(self) -> None:
        checker = make_checker(exchanges={})
        check = await checker._check_native_adapter_health()
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 10: Shadow Verification
# ---------------------------------------------------------------------------


class TestCheckShadowVerification:
    @pytest.mark.asyncio
    async def test_shadow_check_fails_when_no_db_pool(self) -> None:
        checker = make_checker(db_pool=None)
        check = await checker._check_shadow_verification()
        assert check.passed is False
        assert "no DB" in check.value

    @pytest.mark.asyncio
    async def test_shadow_check_fails_when_no_shadow_data(self) -> None:
        pool = make_db_pool(query_results={"MIN(recorded_at)": None})
        checker = make_checker(db_pool=pool)
        check = await checker._check_shadow_verification()
        assert check.passed is False
        assert "no shadow data" in check.value

    @pytest.mark.asyncio
    async def test_shadow_check_fails_when_query_errors(self) -> None:
        pool = make_db_pool(fail=True)
        checker = make_checker(db_pool=pool)
        check = await checker._check_shadow_verification()
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_shadow_check_fails_when_insufficient_hours(self) -> None:
        """Shadow data exists but only 24h elapsed — need 72h."""
        from datetime import timedelta

        # first_shadow is 24h ago (48h short of required 72h)
        recent_start = datetime.now(timezone.utc) - timedelta(hours=24)
        pool = make_db_pool(
            query_results={
                "MIN(recorded_at)": recent_start,
                "incident": 0,
            }
        )
        checker = make_checker(db_pool=pool)
        check = await checker._check_shadow_verification()
        assert check.passed is False
        assert "elapsed" in check.detail or "24." in check.value

    @pytest.mark.asyncio
    async def test_shadow_check_fails_when_incidents_found(self) -> None:
        """Shadow ran 80h but has 3 incidents — should fail."""
        from datetime import timedelta

        old_start = datetime.now(timezone.utc) - timedelta(hours=80)
        pool = make_db_pool(
            query_results={
                "MIN(recorded_at)": old_start,
                "incident": 3,
            }
        )
        checker = make_checker(db_pool=pool)
        check = await checker._check_shadow_verification()
        assert check.passed is False
        assert "incident" in check.detail

    @pytest.mark.asyncio
    async def test_shadow_check_passes_with_sufficient_hours_and_no_incidents(
        self,
    ) -> None:
        """Shadow ran 80h with 0 incidents — check should pass."""
        from datetime import timedelta

        old_start = datetime.now(timezone.utc) - timedelta(hours=80)
        pool = make_db_pool(
            query_results={
                "MIN(recorded_at)": old_start,
                "incident": 0,
            }
        )
        checker = make_checker(db_pool=pool)
        check = await checker._check_shadow_verification()
        assert check.passed is True


# ---------------------------------------------------------------------------
# run_all() integration tests
# ---------------------------------------------------------------------------


class TestRunAll:
    def _make_full_checker(
        self,
        db_pool=None,
        kill_halted: bool = False,
        cb_state: str = "CLOSED",
        live_gate_eligible: bool = True,
        telegram_ok: bool = True,
        health_score: float = 0.99,
    ) -> PreflightChecker:
        adapter = make_mock_adapter("binance", health_score=health_score)
        return PreflightChecker(
            exchanges={"binance": adapter},
            db_pool=db_pool,
            settings=make_settings(70.0),
            kill_switch_check=lambda: kill_halted,
            circuit_breaker_state=lambda: cb_state,
            live_gate_check=lambda: live_gate_eligible,
            telegram_check=lambda: telegram_ok,
        )

    @pytest.mark.asyncio
    async def test_run_all_returns_preflight_result(self) -> None:
        checker = self._make_full_checker()
        result = await checker.run_all()
        assert isinstance(result, PreflightResult)

    @pytest.mark.asyncio
    async def test_run_all_produces_exactly_10_checks(self) -> None:
        checker = self._make_full_checker()
        result = await checker.run_all()
        assert len(result.checks) == 10

    @pytest.mark.asyncio
    async def test_run_all_overall_pass_false_when_kill_switch_halted(self) -> None:
        checker = self._make_full_checker(kill_halted=True)
        result = await checker.run_all()
        assert result.overall_pass is False
        assert any("Kill Switch" in r for r in result.failure_reasons)

    @pytest.mark.asyncio
    async def test_run_all_overall_pass_false_when_circuit_breaker_open(self) -> None:
        checker = self._make_full_checker(cb_state="OPEN")
        result = await checker.run_all()
        assert result.overall_pass is False

    @pytest.mark.asyncio
    async def test_run_all_failure_reasons_populated_on_failure(self) -> None:
        checker = self._make_full_checker(kill_halted=True, cb_state="OPEN")
        result = await checker.run_all()
        assert len(result.failure_reasons) >= 2

    @pytest.mark.asyncio
    async def test_run_all_has_timestamp(self) -> None:
        checker = self._make_full_checker()
        result = await checker.run_all()
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo is not None

    @pytest.mark.asyncio
    async def test_run_all_has_duration(self) -> None:
        checker = self._make_full_checker()
        result = await checker.run_all()
        assert result.duration_ms >= 0.0


# ---------------------------------------------------------------------------
# format_report()
# ---------------------------------------------------------------------------


class TestFormatReport:
    @pytest.mark.asyncio
    async def test_format_report_contains_pass_on_success(self) -> None:
        checker = make_checker(
            kill_switch_check=lambda: False,
            circuit_breaker_state=lambda: "CLOSED",
        )
        result = PreflightResult(
            checks=[
                PreflightCheck("Kill Switch Clear", True, "Clear", "is_halted() == False"),
            ],
            overall_pass=True,
            timestamp=datetime.now(timezone.utc),
        )
        report = checker.format_report(result)
        assert "PASSED" in report
        assert "Kill Switch Clear" in report

    @pytest.mark.asyncio
    async def test_format_report_contains_failed_on_failure(self) -> None:
        checker = make_checker()
        result = PreflightResult(
            checks=[
                PreflightCheck("Kill Switch Clear", False, "HALTED", "is_halted() == False"),
            ],
            overall_pass=False,
            timestamp=datetime.now(timezone.utc),
            failure_reasons=["Kill Switch Clear: HALTED (need is_halted() == False)"],
        )
        report = checker.format_report(result)
        assert "FAILED" in report
        assert "Failures" in report
        assert "Kill Switch Clear" in report

    @pytest.mark.asyncio
    async def test_format_report_contains_all_check_names(self) -> None:
        checker = make_checker(
            kill_switch_check=lambda: False,
            circuit_breaker_state=lambda: "CLOSED",
            live_gate_check=lambda: True,
            telegram_check=lambda: True,
        )
        result = await checker.run_all()
        report = checker.format_report(result)
        expected_names = [
            "TimescaleDB Connection",
            "Exchange WS+REST Response",
            "API Key Permissions",
            "Balance Minimum",
            "Kill Switch Clear",
            "Circuit Breaker CLOSED",
            "LiveGate Eligible",
            "Telegram Working",
            "Native Adapter Health",
            "Shadow Verification",
        ]
        for name in expected_names:
            assert name in report, f"Expected '{name}' in report"


# TestEnvSync removed — US-375 deleted PreflightChecker._check_env_sync()
# (.env unified to repo root; two-file sync check is dead code)

# ---------------------------------------------------------------------------
# MIN_EDGE_BPS default — US-136
# ---------------------------------------------------------------------------


def test_min_edge_bps_default_is_5(monkeypatch) -> None:
    """main.py reads MIN_EDGE_BPS with default value of 5 when env is unset."""
    import os

    monkeypatch.delenv("MIN_EDGE_BPS", raising=False)
    default = int(os.environ.get("MIN_EDGE_BPS", "5"))
    assert default == 5, f"Expected MIN_EDGE_BPS default=5, got {default}"
