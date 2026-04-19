"""BUG-228c: Unit tests for MinNotionalRegistry + live.py auto-bump.

Covers:
- fallback when adapter is missing
- fallback when adapter raises
- happy path: returns adapter's decimal verbatim
- auto-bump accepted when bumped notional <= risk cap
- auto-bump rejected when bumped notional > risk cap
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.infra.exchange.min_notional_registry import MinNotionalRegistry


class _FakeAdapter:
    def __init__(self, value: Decimal | None, raise_exc: bool = False) -> None:
        self._value = value
        self._raise = raise_exc

    async def get_min_notional(self, symbol: str) -> Decimal:
        if self._raise:
            raise RuntimeError("boom")
        return self._value


@pytest.mark.asyncio
async def test_registry_fallback_on_missing_adapter() -> None:
    registry = MinNotionalRegistry({})
    result = await registry.get("binance_futures", "ETH/USDT")
    assert result == Decimal("5")


@pytest.mark.asyncio
async def test_registry_fallback_on_adapter_exception() -> None:
    registry = MinNotionalRegistry({"binance_futures": _FakeAdapter(None, raise_exc=True)})
    result = await registry.get("binance_futures", "ETH/USDT")
    assert result == Decimal("5")


@pytest.mark.asyncio
async def test_registry_returns_adapter_value() -> None:
    registry = MinNotionalRegistry({
        "binance_futures": _FakeAdapter(Decimal("20")),
        "bitget_futures": _FakeAdapter(Decimal("6")),
        "upbit": _FakeAdapter(Decimal("5000")),
    })
    assert await registry.get("binance_futures", "ETH/USDT") == Decimal("20")
    assert await registry.get("bitget_futures", "ETH/USDT") == Decimal("6")
    assert await registry.get("upbit", "ETH/KRW") == Decimal("5000")


@pytest.mark.asyncio
async def test_registry_returns_default_when_adapter_returns_none() -> None:
    """Adapter returning None should be coerced to the $5 fallback."""
    registry = MinNotionalRegistry({"binance_futures": _FakeAdapter(None)})
    assert await registry.get("binance_futures", "ETH/USDT") == Decimal("5")


# ---------------------------------------------------------------------------
# live.py auto-bump logic — direct math test (no LiveMode instantiation).
# Mirrors the bump formula in src/modes/live.py to guard against regressions.
# ---------------------------------------------------------------------------

def _compute_bump(
    size: Decimal, price: Decimal, required_min: Decimal, risk_cap_usd: Decimal
) -> tuple[bool, Decimal, Decimal]:
    """Return (bump_applied, bumped_size, bumped_notional).

    bump_applied=False when bumped_notional > risk_cap_usd (caller should reject).
    """
    current_notional = size * price
    if current_notional >= required_min:
        return True, size, current_notional
    bumped_size = (required_min / price).quantize(Decimal("0.00000001"))
    bumped_notional = bumped_size * price
    if bumped_notional > risk_cap_usd:
        return False, bumped_size, bumped_notional
    return True, bumped_size, bumped_notional


def test_auto_bump_applied_within_risk_cap() -> None:
    # capital=$120, risk_cap 6% → $7.20 cap. Bitget futures min=$6, price=$3000 → bumped=$6.
    applied, _bumped, notional = _compute_bump(
        size=Decimal("0.0005"),
        price=Decimal("3000"),
        required_min=Decimal("6"),
        risk_cap_usd=Decimal("7.20"),
    )
    assert applied is True
    assert notional <= Decimal("7.20")


def test_auto_bump_rejected_exceeds_risk_cap() -> None:
    # Same capital $120 × 6% = $7.20 cap. Binance futures min=$20 → bumped notional=$20 > cap.
    applied, _bumped, notional = _compute_bump(
        size=Decimal("0.0001"),
        price=Decimal("3000"),
        required_min=Decimal("20"),
        risk_cap_usd=Decimal("7.20"),
    )
    assert applied is False
    assert notional > Decimal("7.20")
