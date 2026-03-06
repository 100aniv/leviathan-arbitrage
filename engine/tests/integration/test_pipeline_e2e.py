"""End-to-end pipeline integration tests.

Tests the full signal → strategy → risk → execution pipeline using
InMemoryEventBus and PaperExchangeAdapter (no external dependencies).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.execution.executor import AtomicExecutor, ExecutionResult, ExecutionStatus
from src.execution.paper_adapter import PaperExchangeAdapter
from src.execution.trade_consumer import TradeRequestConsumer
from src.infra.redis.memory_bus import InMemoryEventBus
from src.risk.kill_switch import clear_halt, halt_local, is_halted
from src.strategies.base import TradeRequest, TradeLeg
from src.core.models import OrderSide, OrderType


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def paper_exchanges():
    ex_a = PaperExchangeAdapter(
        exchange_id="test_binance",
        initial_capital=Decimal("1000"),
        spread_injection_rate=0.5,
        spread_injection_bps=50,
        tick_interval=0.01,
    )
    ex_b = PaperExchangeAdapter(
        exchange_id="test_upbit",
        initial_capital=Decimal("1000"),
        spread_injection_rate=0.5,
        spread_injection_bps=60,
        tick_interval=0.01,
    )
    return {"test_binance": ex_a, "test_upbit": ex_b}


@pytest.fixture
def executor(paper_exchanges):
    return AtomicExecutor(exchanges=paper_exchanges)


@pytest.fixture(autouse=True)
def reset_kill_switch():
    clear_halt()
    yield
    clear_halt()


class TestPipelineE2E:
    """Test the full pipeline: EventBus → TradeConsumer → Executor."""

    @pytest.mark.asyncio
    async def test_trade_request_flows_through_pipeline(self, event_bus, executor):
        """Publish a TradeRequest to the event bus and verify execution."""
        results: list[tuple[TradeRequest, ExecutionResult]] = []

        def on_result(req, res):
            results.append((req, res))

        consumer = TradeRequestConsumer(
            event_bus=event_bus,
            executor=executor,
            on_result=on_result,
        )

        await consumer.start()
        await asyncio.sleep(0.05)

        # Publish a trade request
        trade_req = {
            "strategy_id": "test_cross_exchange",
            "legs": [
                {
                    "exchange_id": "test_binance",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "price": "50000",
                    "size": "0.001",
                },
                {
                    "exchange_id": "test_upbit",
                    "symbol": "BTC/USDT",
                    "side": "sell",
                    "order_type": "limit",
                    "price": "50100",
                    "size": "0.001",
                },
            ],
            "expected_profit_usdt": "0.1",
            "confidence": 0.9,
        }

        await event_bus.publish("leviathan:trade_requests", trade_req)
        await asyncio.sleep(0.5)

        await consumer.stop()

        assert consumer.processed_count >= 1
        assert consumer.execution_success_count >= 1

    @pytest.mark.asyncio
    async def test_risk_rejection(self, event_bus, executor):
        """Verify risk check rejection prevents execution."""
        def reject_all(trade_request):
            return False, "risk limit exceeded"

        consumer = TradeRequestConsumer(
            event_bus=event_bus,
            executor=executor,
            risk_check=reject_all,
        )

        await consumer.start()
        await asyncio.sleep(0.05)

        trade_req = {
            "strategy_id": "test_risky",
            "legs": [
                {
                    "exchange_id": "test_binance",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "price": "50000",
                    "size": "0.001",
                },
                {
                    "exchange_id": "test_upbit",
                    "symbol": "BTC/USDT",
                    "side": "sell",
                    "order_type": "limit",
                    "price": "50100",
                    "size": "0.001",
                },
            ],
        }

        await event_bus.publish("leviathan:trade_requests", trade_req)
        await asyncio.sleep(0.3)

        await consumer.stop()

        assert consumer.risk_rejected_count >= 1
        assert consumer.execution_success_count == 0

    @pytest.mark.asyncio
    async def test_kill_switch_halts_processing(self, event_bus, executor):
        """Verify kill switch stops trade processing."""
        consumer = TradeRequestConsumer(
            event_bus=event_bus,
            executor=executor,
        )

        await consumer.start()
        await asyncio.sleep(0.05)

        # Activate kill switch
        halt_local()

        trade_req = {
            "strategy_id": "test_halted",
            "legs": [
                {
                    "exchange_id": "test_binance",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "price": "50000",
                    "size": "0.001",
                },
                {
                    "exchange_id": "test_upbit",
                    "symbol": "BTC/USDT",
                    "side": "sell",
                    "order_type": "limit",
                    "price": "50100",
                    "size": "0.001",
                },
            ],
        }

        await event_bus.publish("leviathan:trade_requests", trade_req)
        await asyncio.sleep(0.3)

        await consumer.stop()

        # Should not execute when halted
        assert consumer.execution_success_count == 0

    @pytest.mark.asyncio
    async def test_same_exchange_execution(self, event_bus, executor):
        """Test same-exchange execution path."""
        results = []

        def on_result(req, res):
            results.append(res)

        consumer = TradeRequestConsumer(
            event_bus=event_bus,
            executor=executor,
            on_result=on_result,
        )

        await consumer.start()
        await asyncio.sleep(0.05)

        trade_req = {
            "strategy_id": "test_same_exchange",
            "legs": [
                {
                    "exchange_id": "test_binance",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "price": "50000",
                    "size": "0.001",
                },
                {
                    "exchange_id": "test_binance",
                    "symbol": "ETH/USDT",
                    "side": "sell",
                    "order_type": "limit",
                    "price": "3000",
                    "size": "0.01",
                },
            ],
        }

        await event_bus.publish("leviathan:trade_requests", trade_req)
        await asyncio.sleep(0.3)

        await consumer.stop()

        assert consumer.processed_count >= 1


class TestPaperExchangeAdapter:
    """Test PaperExchangeAdapter in isolation."""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        adapter = PaperExchangeAdapter(exchange_id="test_paper")
        await adapter.connect()
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_orderbook_generation(self):
        adapter = PaperExchangeAdapter(exchange_id="test_paper")
        ob = await adapter.get_orderbook_snapshot("BTC/USDT")
        assert len(ob.bids) > 0
        assert len(ob.asks) > 0
        assert ob.bids[0].price < ob.asks[0].price

    @pytest.mark.asyncio
    async def test_order_execution_updates_balance(self):
        adapter = PaperExchangeAdapter(
            exchange_id="test_paper",
            initial_capital=Decimal("1000"),
        )

        order = MagicMock()
        order.order_id = "test_1"
        order.client_order_id = "test_client_1"
        order.exchange_id = "test_paper"
        order.symbol = "BTC/USDT"
        order.side = OrderSide.BUY
        order.order_type = OrderType.MARKET
        order.price = Decimal("50000")
        order.amount = Decimal("0.001")
        order.metadata = {}
        order.model_copy = lambda update: type(order)(**{**order.__dict__, **update})

        from src.core.models import Order
        real_order = Order(
            order_id="test_1",
            client_order_id="test_client_1",
            exchange_id="test_paper",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )

        trade = await adapter.place_order(real_order)
        assert trade.amount == Decimal("0.001")

        balances = await adapter.get_balances()
        assert "USDT" in balances
        # USDT should decrease after buying
        assert balances["USDT"].total < Decimal("1000")

    @pytest.mark.asyncio
    async def test_fee_rate(self):
        adapter = PaperExchangeAdapter(exchange_id="test_paper")
        fee = await adapter.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    def test_health_score(self):
        adapter = PaperExchangeAdapter(exchange_id="test_paper")
        assert adapter.health_score == 1.0


class TestInMemoryEventBus:
    """Test InMemoryEventBus."""

    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        bus = InMemoryEventBus()
        await bus.create_consumer_group("test_stream", "test_group")

        await bus.publish("test_stream", {"key": "value"})

        messages = await bus.subscribe(
            stream="test_stream",
            group="test_group",
            consumer="c1",
            count=10,
            block_ms=100,
        )

        assert len(messages) == 1
        assert messages[0]["key"] == "value"

    @pytest.mark.asyncio
    async def test_multiple_consumer_groups(self):
        bus = InMemoryEventBus()
        await bus.create_consumer_group("stream", "group_a")
        await bus.create_consumer_group("stream", "group_b")

        await bus.publish("stream", {"data": "test"})

        msgs_a = await bus.subscribe("stream", "group_a", "c1", count=10, block_ms=100)
        msgs_b = await bus.subscribe("stream", "group_b", "c1", count=10, block_ms=100)

        assert len(msgs_a) == 1
        assert len(msgs_b) == 1

    @pytest.mark.asyncio
    async def test_empty_subscribe_returns_empty(self):
        bus = InMemoryEventBus()
        await bus.create_consumer_group("empty_stream", "group")

        messages = await bus.subscribe(
            "empty_stream", "group", "c1", count=10, block_ms=50,
        )
        assert messages == []
