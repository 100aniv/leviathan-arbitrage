"""Tests for Rate Limit Simulation (US-062).

TDD test suite for:
1. TokenBucket.try_acquire() — non-blocking token acquisition
2. ShadowRateLimiter — per-exchange token bucket management
3. ShadowMode._execute_shadow_trade integration with rate limiting

Run:
    cd engine && python -m pytest tests/test_shadow_rate_limit.py -x --tb=short -v
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from src.infra.exchange.rate_limiter import TokenBucket
from src.modes.shadow import ShadowRateLimiter, ShadowMode
from src.core.models import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50500"),
    volume: Decimal = Decimal("0.1"),
) -> Signal:
    """Build a cross-exchange arbitrage signal (buy < sell, valid spread)."""
    return Signal(
        strategy_id="test_arb",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="upbit",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.01"),
        confidence=0.9,
        volume=volume,
    )


def _make_shadow_mode() -> ShadowMode:
    """Build a ShadowMode with mocked I/O dependencies."""
    mock_executor = MagicMock()
    mock_executor.slippage_model = MagicMock(spec=[])  # no set_context
    return ShadowMode(
        signal_generator=MagicMock(),
        paper_executor=mock_executor,
    )


# ---------------------------------------------------------------------------
# 1. TokenBucket.try_acquire() — unit tests
# ---------------------------------------------------------------------------


def test_try_acquire_basic():
    """try_acquire() returns True when tokens are available."""
    bucket = TokenBucket(rate=5.0, capacity=10)
    assert bucket.try_acquire() is True


def test_try_acquire_exhausted():
    """try_acquire() returns False when token capacity is fully exhausted."""
    bucket = TokenBucket(rate=5.0, capacity=2)
    bucket.try_acquire()
    bucket.try_acquire()
    # Now exhausted — next call must fail
    assert bucket.try_acquire() is False


def test_try_acquire_refill():
    """try_acquire() returns True after _last_refill is backdated to simulate elapsed time."""
    bucket = TokenBucket(rate=10.0, capacity=1)
    bucket.try_acquire()  # exhaust the single token
    assert bucket.try_acquire() is False
    # Backdate _last_refill by 1 second: rate=10 → 10 new tokens added on next refill
    bucket._last_refill -= 1.0
    assert bucket.try_acquire() is True


# ---------------------------------------------------------------------------
# 2. ShadowRateLimiter — unit tests
# ---------------------------------------------------------------------------


def test_shadow_rate_limiter_default_rates():
    """Known exchanges use their EXCHANGE_ORDER_RATES defaults; first acquire succeeds."""
    limiter = ShadowRateLimiter()
    assert limiter.try_acquire("binance") is True
    assert limiter.try_acquire("upbit") is True
    assert limiter.try_acquire("coinone") is True


def test_shadow_rate_limiter_env_override(monkeypatch):
    """SHADOW_RATE_LIMIT_UPBIT='<float>' overrides the token rate for the upbit bucket.

    Format: single float, e.g. '99.0' → rate=99.0 tokens/sec (burst stays at default 8).
    """
    monkeypatch.setenv("SHADOW_RATE_LIMIT_UPBIT", "99.0")
    limiter = ShadowRateLimiter()
    bucket = limiter._get_bucket("upbit")
    assert bucket.rate == 99.0  # overridden from default 8.0


def test_shadow_rate_limiter_prefix_strip():
    """paper_binance and binance resolve to the same underlying token bucket."""
    limiter = ShadowRateLimiter()
    bucket = limiter._get_bucket("binance")
    # Drain all tokens from the binance bucket
    while bucket.try_acquire():
        pass
    # paper_binance should hit the same bucket and be exhausted too
    assert limiter.try_acquire("paper_binance") is False


# ---------------------------------------------------------------------------
# 3. ShadowMode._execute_shadow_trade — rate limit integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_rate_limited_buy():
    """_paper_executor.execute is not called when buy exchange rate limit is exceeded."""
    shadow = _make_shadow_mode()
    shadow._paper_executor.execute = AsyncMock()
    shadow._rate_limiter = MagicMock()
    # Reject binance (buy exchange), allow others
    shadow._rate_limiter.try_acquire.side_effect = lambda ex: ex != "binance"

    await shadow._execute_shadow_trade(_make_signal())

    shadow._paper_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rate_limited_sell():
    """_paper_executor.execute is not called when sell exchange rate limit is exceeded."""
    shadow = _make_shadow_mode()
    shadow._paper_executor.execute = AsyncMock()
    shadow._rate_limiter = MagicMock()
    # Allow buy (binance), reject sell (upbit)
    shadow._rate_limiter.try_acquire.side_effect = lambda ex: ex != "upbit"

    await shadow._execute_shadow_trade(_make_signal())

    shadow._paper_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_before_balance():
    """balance_tracker.deduct is not called when rate limit blocks the trade."""
    shadow = _make_shadow_mode()
    shadow._paper_executor.execute = AsyncMock()
    shadow._balance_tracker = MagicMock()
    shadow._rate_limiter = MagicMock()
    shadow._rate_limiter.try_acquire.return_value = False

    await shadow._execute_shadow_trade(_make_signal())

    shadow._balance_tracker.deduct.assert_not_called()


@pytest.mark.asyncio
async def test_stats_rate_limited():
    """stats.trades_rate_limited increments by 1 each time a rate limit is hit."""
    shadow = _make_shadow_mode()
    shadow._paper_executor.execute = AsyncMock()
    shadow._rate_limiter = MagicMock()
    shadow._rate_limiter.try_acquire.return_value = False

    await shadow._execute_shadow_trade(_make_signal())

    assert shadow._stats.trades_rate_limited == 1


@pytest.mark.asyncio
async def test_structlog_warning():
    """shadow_mode.rate_limit_exceeded warning is emitted when rate limit is hit."""
    shadow = _make_shadow_mode()
    shadow._paper_executor.execute = AsyncMock()
    shadow._rate_limiter = MagicMock()
    shadow._rate_limiter.try_acquire.return_value = False

    with structlog.testing.capture_logs() as cap_logs:
        await shadow._execute_shadow_trade(_make_signal())

    events = [e["event"] for e in cap_logs]
    assert "shadow_mode.rate_limit_exceeded" in events
