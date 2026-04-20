"""Day 8 — OrderRouter tests.

Covers:
1. Flag OFF (default) → bypass, direct adapter call, no dedup, no journal.
2. Flag ON + basic submit → RouteResult with formatted client_order_id.
3. Flag ON + duplicate submit (same trace_id/leg_index) → cached RouteResult, adapter called once.
4. Flag ON + TTL expiry → cache evicts, second submit triggers new adapter call.
5. client_order_id format: f"{trace_id}.{leg_index}" regardless of flag state.
6. Flag ON + state_machine supplied → SENT transition emitted before adapter call.
7. Flag ON + adapter raises → exception propagates, no dedup entry created.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.execution.router import OrderRouter, RouteResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeOrder:
    symbol: str
    side: str
    qty: float


@dataclass
class _FakeAdapterResponse:
    order_id: str
    filled: float = 0.0


class _FakeAdapter:
    """Minimal adapter stub with configurable response and raise."""

    def __init__(
        self,
        response: _FakeAdapterResponse | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._response = response or _FakeAdapterResponse(order_id="EX-1")
        self._raises = raises
        self.calls: list[_FakeOrder] = []

    async def place_order(self, order: _FakeOrder) -> _FakeAdapterResponse:
        self.calls.append(order)
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeTransitionEvent:
    def __init__(self, seq: int) -> None:
        self.seq = seq


class _FakeStateMachine:
    """Minimal OrderStateMachine stub: records transitions, returns stub events."""

    def __init__(self) -> None:
        self.transitions: list[dict[str, Any]] = []
        self._next_seq = 100

    async def transition(
        self,
        order_id: str,
        from_state: str,
        to_state: str,
        payload: dict[str, Any],
    ) -> _FakeTransitionEvent:
        self.transitions.append(
            {
                "order_id": order_id,
                "from_state": from_state,
                "to_state": to_state,
                "payload": payload,
            }
        )
        seq = self._next_seq
        self._next_seq += 1
        return _FakeTransitionEvent(seq=seq)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enable_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_ROUTER_ENABLED", "true")


@pytest.fixture
def disable_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_ROUTER_ENABLED", "false")


@pytest.fixture
def order() -> _FakeOrder:
    return _FakeOrder(symbol="BTCUSDT", side="buy", qty=0.5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_bypasses_dedup_and_journal(
    disable_router: None, order: _FakeOrder
) -> None:
    """Flag OFF → direct adapter call, no dedup, no journal_event_seq."""
    adapter = _FakeAdapter(response=_FakeAdapterResponse(order_id="EX-BYPASS"))
    sm = _FakeStateMachine()
    router = OrderRouter(state_machine=sm)

    r1 = await router.submit(order, adapter, trace_id="t1", leg_index=0)
    r2 = await router.submit(order, adapter, trace_id="t1", leg_index=0)

    # Adapter called each time (no dedup in bypass mode).
    assert len(adapter.calls) == 2
    # State machine untouched.
    assert sm.transitions == []
    # client_order_id still formatted correctly.
    assert r1.client_order_id == "t1.0"
    assert r2.client_order_id == "t1.0"
    assert r1.journal_event_seq is None
    assert r2.journal_event_seq is None
    assert r1.order_id == "EX-BYPASS"


@pytest.mark.asyncio
async def test_submit_returns_route_result_with_order_id(
    enable_router: None, order: _FakeOrder
) -> None:
    adapter = _FakeAdapter(response=_FakeAdapterResponse(order_id="EX-42"))
    router = OrderRouter()

    result = await router.submit(order, adapter, trace_id="abc", leg_index=0)

    assert isinstance(result, RouteResult)
    assert result.order_id == "EX-42"
    assert result.client_order_id == "abc.0"
    assert result.state == "SENT"
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_duplicate_submit_returns_cached_result(
    enable_router: None, order: _FakeOrder
) -> None:
    """Re-submitting same (trace_id, leg_index) returns cached RouteResult; adapter called once."""
    adapter = _FakeAdapter(response=_FakeAdapterResponse(order_id="EX-ONCE"))
    router = OrderRouter()

    r1 = await router.submit(order, adapter, trace_id="trace-xyz", leg_index=2)
    r2 = await router.submit(order, adapter, trace_id="trace-xyz", leg_index=2)

    assert r1.order_id == "EX-ONCE"
    assert r2.order_id == "EX-ONCE"
    assert r1.client_order_id == r2.client_order_id == "trace-xyz.2"
    # Adapter called exactly once (second call deduped).
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_ttl_expiry_evicts_cache_entry(
    enable_router: None, order: _FakeOrder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After TTL, second submit with same client_order_id performs new adapter call."""
    adapter = _FakeAdapter(response=_FakeAdapterResponse(order_id="EX-FIRST"))
    router = OrderRouter()

    # Control time.monotonic via patching the router's time source.
    current = [1000.0]

    def _fake_monotonic() -> float:
        return current[0]

    monkeypatch.setattr("src.execution.router.time.monotonic", _fake_monotonic)

    r1 = await router.submit(order, adapter, trace_id="ttl", leg_index=0)
    assert r1.order_id == "EX-FIRST"
    assert len(adapter.calls) == 1

    # Advance time past TTL (600 s default).
    current[0] += 601.0

    # Swap adapter response so we can detect the re-call.
    adapter._response = _FakeAdapterResponse(order_id="EX-SECOND")

    r2 = await router.submit(order, adapter, trace_id="ttl", leg_index=0)
    assert r2.order_id == "EX-SECOND"
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_client_order_id_format(
    enable_router: None, order: _FakeOrder
) -> None:
    """client_order_id = f'{trace_id}.{leg_index}'."""
    adapter = _FakeAdapter()
    router = OrderRouter()

    r = await router.submit(order, adapter, trace_id="abc", leg_index=1)
    assert r.client_order_id == "abc.1"


@pytest.mark.asyncio
async def test_state_machine_sent_emission_before_adapter(
    enable_router: None, order: _FakeOrder
) -> None:
    """With state_machine supplied and flag on, SENT transition emitted; seq captured."""
    adapter = _FakeAdapter(response=_FakeAdapterResponse(order_id="EX-SM"))
    sm = _FakeStateMachine()
    router = OrderRouter(state_machine=sm)

    r = await router.submit(order, adapter, trace_id="sm-trace", leg_index=3)

    assert len(sm.transitions) == 1
    entry = sm.transitions[0]
    assert entry["order_id"] == "sm-trace.3"
    assert entry["from_state"] == "PENDING"
    assert entry["to_state"] == "SENT"
    assert r.journal_event_seq == 100


@pytest.mark.asyncio
async def test_adapter_raise_no_dedup_entry(
    enable_router: None, order: _FakeOrder
) -> None:
    """Adapter raises → exception propagates, no dedup entry; retry calls adapter again."""
    adapter = _FakeAdapter(raises=RuntimeError("network"))
    router = OrderRouter()

    with pytest.raises(RuntimeError, match="network"):
        await router.submit(order, adapter, trace_id="err", leg_index=0)

    # No dedup entry: retry must invoke adapter again.
    adapter._raises = None
    adapter._response = _FakeAdapterResponse(order_id="EX-RETRY")
    r = await router.submit(order, adapter, trace_id="err", leg_index=0)
    assert r.order_id == "EX-RETRY"
    assert len(adapter.calls) == 2
