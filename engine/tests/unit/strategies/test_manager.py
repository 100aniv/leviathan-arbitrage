"""Tests for StrategyManager."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.statistical_arb import StatisticalArbStrategy
from src.strategies.latency_arb import LatencyArbStrategy
from src.strategies.manager import CONSUMER_GROUP, SIGNAL_STREAM, StrategyManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_event_bus(messages: list[dict[str, Any]] | None = None) -> MagicMock:
    bus = MagicMock()
    bus.create_consumer_group = AsyncMock()
    bus.subscribe = AsyncMock(return_value=messages or [])
    bus.publish = AsyncMock()
    bus.ack_message = AsyncMock()
    return bus


def make_cross_exchange_strategy(
    strategy_id: str = "cross_exchange_spot_v1",
    cost: Decimal = Decimal("1"),
) -> CrossExchangeStrategy:
    return CrossExchangeStrategy(
        strategy_id, make_calculator(cost), CrossExchangeConfig(min_spread_bps=Decimal("10"))
    )


def make_signal_event_dict(
    strategy_id: str = "cross_exchange_spot_v1",
    spread_pct: Decimal = Decimal("0.002"),
) -> dict[str, Any]:
    signal = Signal(
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=spread_pct,
        confidence=0.9,
        volume=Decimal("0.5"),
        timestamp=datetime.now(timezone.utc),
    )
    return {
        "event_type": "signal",
        "event_id": "test-event-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "price_hub",
        "signal": signal.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_strategy():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    assert "cross_exchange_spot_v1" in manager.list_strategies()


def test_deregister_strategy():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    manager.deregister("cross_exchange_spot_v1")
    assert "cross_exchange_spot_v1" not in manager.list_strategies()


def test_deregister_nonexistent_no_error():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    manager.deregister("nonexistent")  # Should not raise


def test_get_strategy_returns_correct():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    assert manager.get_strategy("cross_exchange_spot_v1") is strategy


def test_get_nonexistent_strategy_returns_none():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    assert manager.get_strategy("none") is None


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_strategy():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    await manager.start_strategy("cross_exchange_spot_v1")
    assert strategy.is_active


@pytest.mark.asyncio
async def test_stop_strategy():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    await manager.start_strategy("cross_exchange_spot_v1")
    await manager.stop_strategy("cross_exchange_spot_v1")
    assert not strategy.is_active


@pytest.mark.asyncio
async def test_start_unregistered_raises():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    with pytest.raises(KeyError):
        await manager.start_strategy("nonexistent")


@pytest.mark.asyncio
async def test_reconfigure_strategy_at_runtime():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    await manager.start_strategy("cross_exchange_spot_v1")

    new_config = CrossExchangeConfig(min_spread_bps=Decimal("50"))
    await manager.reconfigure("cross_exchange_spot_v1", new_config)

    assert strategy.config.min_spread_bps == Decimal("50")
    assert strategy.is_active  # re-activated after reconfigure


@pytest.mark.asyncio
async def test_reconfigure_stopped_strategy_stays_stopped():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy()
    manager.register(strategy)
    # Not started

    new_config = CrossExchangeConfig(min_spread_bps=Decimal("30"))
    await manager.reconfigure("cross_exchange_spot_v1", new_config)

    assert not strategy.is_active
    assert strategy.config.min_spread_bps == Decimal("30")


# ---------------------------------------------------------------------------
# Signal routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_consumer_group():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    # Patch _consume_loop so it doesn't actually run
    manager._consume_loop = AsyncMock()
    await manager.start()
    bus.create_consumer_group.assert_awaited_once_with(
        stream=SIGNAL_STREAM, group=CONSUMER_GROUP, start_id="$"
    )
    await manager.stop()


@pytest.mark.asyncio
async def test_dispatch_routes_signal_to_matching_strategy():
    """Signal with strategy_id containing STRATEGY_TYPE gets routed."""
    bus = make_event_bus()
    manager = StrategyManager(bus)
    strategy = make_cross_exchange_strategy("cex_spot_v1")
    manager.register(strategy)
    await strategy.start()

    event_dict = make_signal_event_dict(strategy_id="cross_exchange_spot_v1")
    await manager._dispatch(event_dict)

    # Strategy should have received the signal
    assert strategy.metrics.signals_received == 1


@pytest.mark.asyncio
async def test_dispatch_bad_event_no_crash():
    """Malformed event should log warning but not raise."""
    bus = make_event_bus()
    manager = StrategyManager(bus)
    await manager._dispatch({"bad": "data"})  # Should not raise


@pytest.mark.asyncio
async def test_emit_trade_request_publishes_to_stream():
    bus = make_event_bus()
    manager = StrategyManager(bus)

    from src.strategies.base import TradeLeg

    req = TradeRequest(
        strategy_id="test",
        legs=[
            TradeLeg(exchange_id="binance", symbol="BTC/USDT", side=OrderSide.BUY, size=Decimal("0.1"))
        ],
        expected_profit_usdt=Decimal("10"),
    )
    await manager._emit_trade_request(req)
    bus.publish.assert_awaited_once()
    call_args = bus.publish.call_args
    assert call_args[0][0] == "leviathan:trade_requests"


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------


def test_get_metrics_returns_per_strategy():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    s1 = make_cross_exchange_strategy("s1")
    s2 = make_cross_exchange_strategy("s2")
    manager.register(s1)
    manager.register(s2)
    metrics = manager.get_metrics()
    assert "s1" in metrics
    assert "s2" in metrics


def test_get_all_metrics_summary():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    s1 = make_cross_exchange_strategy("s1")
    manager.register(s1)
    summary = manager.get_all_metrics_summary()
    assert "total_signals_received" in summary
    assert "total_trade_requests" in summary
    assert "s1" in summary["strategies"]


@pytest.mark.asyncio
async def test_stop_manager_stops_all_strategies():
    bus = make_event_bus()
    manager = StrategyManager(bus)
    s1 = make_cross_exchange_strategy("s1")
    manager.register(s1)
    await manager.start_strategy("s1")

    manager._consume_loop = AsyncMock()
    await manager.start()
    await manager.stop()

    assert not s1.is_active


# ---------------------------------------------------------------------------
# route_signal tests (US-023, Task #6)
# ---------------------------------------------------------------------------


def _make_signal(
    strategy_id: str = "cross_exchange_spot_v1",
    spread_pct: Decimal = Decimal("0.005"),
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50250"),
        spread_pct=spread_pct,
        confidence=0.9,
        volume=Decimal("0.5"),
        timestamp=datetime.now(timezone.utc),
    )


def _make_trade_request(strategy_id: str = "cross_exchange_spot_v1") -> TradeRequest:
    return TradeRequest(
        strategy_id=strategy_id,
        legs=[
            TradeLeg(
                exchange_id="binance",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                size=Decimal("0.1"),
            ),
            TradeLeg(
                exchange_id="okx",
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                size=Decimal("0.1"),
            ),
        ],
        expected_profit_usdt=Decimal("5"),
    )


@pytest.mark.asyncio
async def test_route_signal_dispatches_to_matching_strategy():
    """_should_route() matching strategy gets on_signal() called."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    strategy = make_cross_exchange_strategy("cross_exchange_spot_v1")
    await strategy.start()
    trade_req = _make_trade_request()
    strategy.on_signal = AsyncMock(return_value=trade_req)
    manager.register(strategy)

    signal = _make_signal(strategy_id="cross_exchange_spot_v1")
    results = await manager.route_signal(signal)

    strategy.on_signal.assert_awaited_once_with(signal)
    assert len(results) == 1
    assert results[0] is trade_req


@pytest.mark.asyncio
async def test_route_signal_returns_trade_requests():
    """Returns list of TradeRequest from all accepting strategies."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    s1 = make_cross_exchange_strategy("s1")
    await s1.start()
    req1 = _make_trade_request("s1")
    s1.on_signal = AsyncMock(return_value=req1)

    s2 = make_cross_exchange_strategy("s2")
    await s2.start()
    req2 = _make_trade_request("s2")
    s2.on_signal = AsyncMock(return_value=req2)

    manager.register(s1)
    manager.register(s2)

    # "cross_exchange_spot" matches STRATEGY_TYPE of both
    signal = _make_signal(strategy_id="cross_exchange_spot")
    results = await manager.route_signal(signal)

    assert len(results) == 2
    assert req1 in results
    assert req2 in results


@pytest.mark.asyncio
async def test_route_signal_skips_inactive_strategies():
    """is_active=False strategies are not called."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    strategy = make_cross_exchange_strategy()
    # Not started → is_active=False
    strategy.on_signal = AsyncMock(return_value=_make_trade_request())
    manager.register(strategy)

    signal = _make_signal(strategy_id="cross_exchange_spot_v1")
    results = await manager.route_signal(signal)

    strategy.on_signal.assert_not_awaited()
    assert results == []


@pytest.mark.asyncio
async def test_route_signal_skips_non_matching_type():
    """STRATEGY_TYPE mismatch → strategy not called."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    # FundingRateStrategy.STRATEGY_TYPE = "funding_rate_arb"
    strategy = FundingRateStrategy("funding_rate_v1", make_calculator())
    await strategy.start()
    strategy.on_signal = AsyncMock(return_value=_make_trade_request())
    manager.register(strategy)

    # Signal is for cross_exchange, not funding_rate
    signal = _make_signal(strategy_id="cross_exchange_spot_v1")
    results = await manager.route_signal(signal)

    strategy.on_signal.assert_not_awaited()
    assert results == []


@pytest.mark.asyncio
async def test_route_signal_returns_empty_when_all_filtered():
    """All strategies return None → empty list returned (no fallback)."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    strategy = make_cross_exchange_strategy()
    await strategy.start()
    strategy.on_signal = AsyncMock(return_value=None)  # filtered by strategy
    manager.register(strategy)

    signal = _make_signal(strategy_id="cross_exchange_spot_v1")
    results = await manager.route_signal(signal)

    strategy.on_signal.assert_awaited_once()
    assert results == []


@pytest.mark.asyncio
async def test_route_signal_handles_strategy_exception():
    """One strategy raises → other strategies still called, exception is swallowed."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    failing = make_cross_exchange_strategy("failing")
    await failing.start()
    failing.on_signal = AsyncMock(side_effect=RuntimeError("boom"))

    ok = make_cross_exchange_strategy("ok")
    await ok.start()
    ok_req = _make_trade_request("ok")
    ok.on_signal = AsyncMock(return_value=ok_req)

    manager.register(failing)
    manager.register(ok)

    signal = _make_signal(strategy_id="cross_exchange_spot")
    results = await manager.route_signal(signal)

    # The failing strategy was called
    failing.on_signal.assert_awaited_once()
    # The ok strategy was still called and its result is returned
    ok.on_signal.assert_awaited_once()
    assert len(results) == 1
    assert results[0] is ok_req


@pytest.mark.asyncio
async def test_cross_exchange_signal_routes_to_statistical_arb():
    """cross_exchange signals should also route to statistical_arb (derived strategy)."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    strategy = StatisticalArbStrategy("stat_arb_v1", make_calculator())
    await strategy.start()
    strategy.on_signal = AsyncMock(return_value=None)
    manager.register(strategy)

    signal = _make_signal(strategy_id="cross_exchange_spot")
    await manager.route_signal(signal)

    strategy.on_signal.assert_awaited_once()


@pytest.mark.asyncio
async def test_cross_exchange_signal_routes_to_latency_arb():
    """cross_exchange signals should also route to latency_arb (derived strategy)."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    tracker = MagicMock()
    tracker.get_latency_info = MagicMock(return_value=None)
    strategy = LatencyArbStrategy("latency_arb_v1", make_calculator(), tracker)
    await strategy.start()
    strategy.on_signal = AsyncMock(return_value=None)
    manager.register(strategy)

    signal = _make_signal(strategy_id="cross_exchange_spot")
    await manager.route_signal(signal)

    strategy.on_signal.assert_awaited_once()


@pytest.mark.asyncio
async def test_funding_rate_signal_not_routed_to_statistical_arb():
    """Non-cross_exchange signals should NOT route to statistical_arb."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    strategy = StatisticalArbStrategy("stat_arb_v1", make_calculator())
    await strategy.start()
    strategy.on_signal = AsyncMock(return_value=None)
    manager.register(strategy)

    signal = _make_signal(strategy_id="funding_rate_arb")
    await manager.route_signal(signal)

    strategy.on_signal.assert_not_awaited()


