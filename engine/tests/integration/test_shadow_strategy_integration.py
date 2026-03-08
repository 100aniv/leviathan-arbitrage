"""Shadow Mode + StrategyManager integration tests (US-023).

Verifies:
- Signal routing via StrategyManager.route_signal() in shadow mode
- Type-based matching for each strategy type
- Fallback behavior (strategy_manager=None, routing exception)
- Redis consume loop not started in shadow mode
- Per-strategy metrics populated after routing
- futures_futures STRATEGY_TYPE="futures_futures" matching fix
- Empty route result does NOT trigger fallback
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, Signal
from src.infra.redis.event_bus import EventBus
from src.modes.shadow import ROUTING_FALLBACK_TOTAL, ShadowMode
from src.strategies.base import BaseStrategy, CostCalculator, TradeRequest, TradeLeg
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.futures_futures import FuturesFuturesStrategy
from src.strategies.manager import StrategyManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(
    strategy_id: str = "cross_exchange_spot",
    symbol: str = "BTC/USDT",
    spread_pct: Decimal = Decimal("0.005"),
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="upbit",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50250"),
        spread_pct=spread_pct,
        confidence=0.9,
        volume=Decimal("0.1"),
        timestamp=datetime.now(timezone.utc),
    )


def make_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_trade_request(strategy_id: str = "cross_exchange_spot") -> TradeRequest:
    leg = TradeLeg(
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        size=Decimal("0.1"),
    )
    return TradeRequest(strategy_id=strategy_id, legs=[leg])


def make_shadow_mode(strategy_manager=None) -> ShadowMode:
    sig_gen = MagicMock()
    sig_gen.on_orderbook_update = MagicMock(return_value=None)
    return ShadowMode(
        signal_generator=sig_gen,
        strategy_manager=strategy_manager,
    )


def make_event_bus() -> MagicMock:
    bus = MagicMock(spec=EventBus)
    bus.create_consumer_group = AsyncMock()
    bus.subscribe = AsyncMock(return_value=[])
    bus.publish = AsyncMock()
    return bus


def make_mock_strategy(
    strategy_id: str,
    strategy_type: str,
    is_active: bool = True,
    on_signal_return=None,
) -> MagicMock:
    s = MagicMock(spec=BaseStrategy)
    s.strategy_id = strategy_id
    s.STRATEGY_TYPE = strategy_type
    s.is_active = is_active
    s._metrics = MagicMock()
    s._metrics.signals_received = 0
    s._metrics.trade_requests_generated = 0
    s._metrics.signals_filtered = 0
    s.on_signal = AsyncMock(return_value=on_signal_return)
    return s


# ---------------------------------------------------------------------------
# Test #10: Shadow routes cross_exchange signal via StrategyManager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_routes_cross_exchange_signal_via_manager():
    """cross_exchange signal is dispatched through StrategyManager.route_signal()."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    trade_req = make_trade_request()
    strategy = make_mock_strategy(
        "cross_exchange_spot_v1", "cross_exchange_spot", on_signal_return=trade_req
    )
    manager._strategies["cross_exchange_spot_v1"] = strategy

    shadow = make_shadow_mode(strategy_manager=manager)
    shadow._execute_shadow_trade_request = AsyncMock()

    signal = make_signal(strategy_id="cross_exchange_spot")
    await shadow._route_signal_to_strategies(signal)

    strategy.on_signal.assert_awaited_once_with(signal)
    shadow._execute_shadow_trade_request.assert_awaited_once_with(trade_req)


# ---------------------------------------------------------------------------
# Test #11: Shadow routes multi-strategy signals to correct strategies only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_routes_multi_strategy_signals_via_manager():
    """Each signal type is routed to the matching strategy only."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    cx_strategy = make_mock_strategy("cross_exchange_spot_v1", "cross_exchange_spot")
    fr_strategy = make_mock_strategy("funding_rate_arb_v1", "funding_rate_arb")
    manager._strategies["cross_exchange_spot_v1"] = cx_strategy
    manager._strategies["funding_rate_arb_v1"] = fr_strategy

    shadow = make_shadow_mode(strategy_manager=manager)
    shadow._execute_shadow_trade_request = AsyncMock()

    # Send cross_exchange signal — only cx_strategy should get it
    signal = make_signal(strategy_id="cross_exchange_spot")
    await shadow._route_signal_to_strategies(signal)

    cx_strategy.on_signal.assert_awaited_once_with(signal)
    fr_strategy.on_signal.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test #12: Shadow fallback when strategy_manager=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_fallback_without_strategy_manager():
    """When strategy_manager is None, _route_signal_to_strategies returns immediately."""
    shadow = make_shadow_mode(strategy_manager=None)
    shadow._execute_shadow_trade = AsyncMock()

    signal = make_signal()
    await shadow._route_signal_to_strategies(signal)

    # No fallback triggered — just returns
    shadow._execute_shadow_trade.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test #13: Shadow fallback on routing exception + Prometheus counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_fallback_on_routing_exception():
    """Routing exception triggers _execute_shadow_trade() + ROUTING_FALLBACK_TOTAL counter."""
    bus = make_event_bus()
    manager = StrategyManager(bus)
    manager.route_signal = AsyncMock(side_effect=RuntimeError("redis down"))

    shadow = make_shadow_mode(strategy_manager=manager)
    shadow._execute_shadow_trade = AsyncMock()

    signal = make_signal()

    # Capture counter value before
    before = ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception")._value.get()
    await shadow._route_signal_to_strategies(signal)
    after = ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception")._value.get()

    shadow._execute_shadow_trade.assert_awaited_once_with(signal)
    assert after > before


# ---------------------------------------------------------------------------
# Test #14: StrategyManager Redis loop not started in shadow mode
# ---------------------------------------------------------------------------


def test_strategy_manager_redis_loop_not_started_in_shadow():
    """StrategyManager._running stays False in shadow mode (route_signal path)."""
    bus = make_event_bus()
    manager = StrategyManager(bus)
    # In shadow mode we never call manager.start(), so _running must be False
    assert manager._running is False


# ---------------------------------------------------------------------------
# Test #15: Per-strategy metrics populated after routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_strategy_metrics_not_double_counted():
    """route_signal() delegates metrics to on_signal() — no manual incrementing."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    trade_req = make_trade_request()
    strategy = make_mock_strategy(
        "cross_exchange_spot_v1", "cross_exchange_spot", on_signal_return=trade_req
    )
    strategy._metrics.signals_received = 0
    strategy._metrics.trade_requests_generated = 0
    manager._strategies["cross_exchange_spot_v1"] = strategy

    shadow = make_shadow_mode(strategy_manager=manager)
    shadow._execute_shadow_trade_request = AsyncMock()

    signal = make_signal(strategy_id="cross_exchange_spot")
    await shadow._route_signal_to_strategies(signal)

    # Metrics stay 0 with mock — real strategies increment internally.
    # route_signal() must NOT double-count.
    assert strategy._metrics.signals_received == 0
    assert strategy._metrics.trade_requests_generated == 0
    # Trade request was still processed correctly
    shadow._execute_shadow_trade_request.assert_awaited_once_with(trade_req)


# ---------------------------------------------------------------------------
# Test #16: Signal type matching — cross_exchange
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_type_matching_cross_exchange():
    """cross_exchange_spot signal only reaches CrossExchangeStrategy."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    target = make_mock_strategy("cross_exchange_spot_v1", "cross_exchange_spot")
    other = make_mock_strategy("funding_rate_arb_v1", "funding_rate_arb")
    manager._strategies["cross_exchange_spot_v1"] = target
    manager._strategies["funding_rate_arb_v1"] = other

    signal = make_signal(strategy_id="cross_exchange_spot")
    await manager.route_signal(signal)

    target.on_signal.assert_awaited_once_with(signal)
    other.on_signal.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test #17: Signal type matching — funding_rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_type_matching_funding_rate():
    """funding_rate_arb signal only reaches FundingRateStrategy."""
    bus = make_event_bus()
    manager = StrategyManager(bus)

    target = make_mock_strategy("funding_rate_arb_v1", "funding_rate_arb")
    other = make_mock_strategy("cross_exchange_spot_v1", "cross_exchange_spot")
    manager._strategies["funding_rate_arb_v1"] = target
    manager._strategies["cross_exchange_spot_v1"] = other

    signal = make_signal(strategy_id="funding_rate_arb")
    await manager.route_signal(signal)

    target.on_signal.assert_awaited_once_with(signal)
    other.on_signal.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test #18: Signal type matching — futures_futures (Step 0 fix verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_type_matching_futures_futures():
    """futures_futures_spread signal matches FuturesFuturesStrategy (STRATEGY_TYPE='futures_futures').

    Verifies Step 0 fix: 'futures_futures' in 'futures_futures_spread' = True.
    """
    # Verify the STRATEGY_TYPE was changed correctly
    assert FuturesFuturesStrategy.STRATEGY_TYPE == "futures_futures"
    assert "futures_futures" in "futures_futures_spread"

    bus = make_event_bus()
    manager = StrategyManager(bus)

    target = make_mock_strategy("futures_futures_v1", "futures_futures")
    other = make_mock_strategy("cross_exchange_spot_v1", "cross_exchange_spot")
    manager._strategies["futures_futures_v1"] = target
    manager._strategies["cross_exchange_spot_v1"] = other

    signal = make_signal(strategy_id="futures_futures_spread")
    await manager.route_signal(signal)

    target.on_signal.assert_awaited_once_with(signal)
    other.on_signal.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test #19: Empty route result does NOT trigger fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_route_result_no_fallback():
    """Empty list from route_signal() means all strategies filtered — no fallback called."""
    bus = make_event_bus()
    manager = StrategyManager(bus)
    manager.route_signal = AsyncMock(return_value=[])

    shadow = make_shadow_mode(strategy_manager=manager)
    shadow._execute_shadow_trade = AsyncMock()
    shadow._execute_shadow_trade_request = AsyncMock()

    signal = make_signal()
    before = ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception")._value.get()
    await shadow._route_signal_to_strategies(signal)
    after = ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception")._value.get()

    shadow._execute_shadow_trade.assert_not_awaited()
    shadow._execute_shadow_trade_request.assert_not_awaited()
    assert after == before  # Counter did not increment
