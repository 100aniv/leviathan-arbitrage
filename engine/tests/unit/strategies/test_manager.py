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
from src.strategies.base import BaseStrategy, CostCalculator, TradeRequest
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
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
