"""Gate test shared fixtures: mock exchanges, simulated engine context."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.risk.kill_switch import KillSwitch, _HALT_FLAG, clear_halt
from src.risk.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Reset halt flag around every gate test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_halt_flag():
    """Reset global halt flag before and after each gate test."""
    _HALT_FLAG.clear()
    yield
    _HALT_FLAG.clear()


# ---------------------------------------------------------------------------
# Mock Exchange Adapter satisfying both KillSwitch and AtomicExecutor APIs
# ---------------------------------------------------------------------------


class MockExchangeAdapter:
    """Simulated exchange adapter for gate-level validation tests."""

    def __init__(
        self,
        exchange_id: str,
        fill_latency_ms: float = 5.0,
        health_score: float = 1.0,
    ) -> None:
        self.exchange_id = exchange_id
        self._fill_latency_ms = fill_latency_ms
        self.health_score: float = health_score
        self.is_connected: bool = True
        self._fills: list[dict] = []
        self._cancelled_orders: list[str] = []
        self._closed_positions: list[str] = []
        self._order_counter: int = 0

    async def place_order(self, order: Any) -> Any:
        """Simulate order placement with configurable latency."""
        await asyncio.sleep(self._fill_latency_ms / 1000.0)
        self._order_counter += 1
        trade = MagicMock()
        trade.trade_id = str(uuid.uuid4())
        trade.order_id = str(uuid.uuid4())
        trade.exchange_id = self.exchange_id
        trade.symbol = getattr(order, "symbol", "BTC/USDT")
        trade.side = getattr(order, "side", "buy")
        trade.price = getattr(order, "price", None) or Decimal("50000")
        trade.amount = getattr(order, "amount", Decimal("0.01"))
        trade.fee = trade.amount * trade.price * Decimal("0.001")
        self._fills.append({"order_id": trade.order_id, "trade": trade})
        return trade

    async def cancel_order(self, order_id: str) -> None:
        self._cancelled_orders.append(order_id)

    async def cancel_all_orders(self, timeout_ms: int = 2000) -> list[str]:
        cancelled = [f"ord_{self.exchange_id}_{i}" for i in range(3)]
        self._cancelled_orders.extend(cancelled)
        return cancelled

    async def close_all_positions(self, timeout_ms: int = 3000) -> list[str]:
        closed = [f"pos_{self.exchange_id}_{i}" for i in range(2)]
        self._closed_positions.extend(closed)
        return closed

    async def get_orderbook_snapshot(self, symbol: str) -> Any:
        ob = MagicMock()
        ob.bids = [[Decimal("50000"), Decimal("1.0")]]
        ob.asks = [[Decimal("50001"), Decimal("1.0")]]
        return ob

    async def get_positions(self) -> list:
        return []


# ---------------------------------------------------------------------------
# Exchange fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_binance() -> MockExchangeAdapter:
    return MockExchangeAdapter("binance", fill_latency_ms=5.0)


@pytest.fixture
def mock_okx() -> MockExchangeAdapter:
    return MockExchangeAdapter("okx", fill_latency_ms=7.0)


@pytest.fixture
def mock_bybit() -> MockExchangeAdapter:
    return MockExchangeAdapter("bybit", fill_latency_ms=8.0)


@pytest.fixture
def mock_exchanges(
    mock_binance: MockExchangeAdapter,
    mock_okx: MockExchangeAdapter,
    mock_bybit: MockExchangeAdapter,
) -> dict[str, MockExchangeAdapter]:
    return {"binance": mock_binance, "okx": mock_okx, "bybit": mock_bybit}


# ---------------------------------------------------------------------------
# Kill switch & circuit breaker fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kill_switch(mock_exchanges: dict) -> KillSwitch:
    return KillSwitch(
        redis_client=None,
        exchanges=list(mock_exchanges.values()),
        tier3_enabled=True,
    )


@pytest.fixture
def circuit_breaker() -> CircuitBreaker:
    return CircuitBreaker(
        mdd_threshold=0.02,
        consecutive_loss_limit=5,
        api_error_rate_threshold=0.20,
        cooldown_seconds=0.05,  # short for tests
    )


# ---------------------------------------------------------------------------
# Simulated PnL / trade history generators
# ---------------------------------------------------------------------------


def make_winning_trade(size: Decimal = Decimal("0.5")) -> dict:
    """Simulate a profitable arbitrage trade.

    Uses VIP maker fee (0.03% per leg) and 0.30% gross spread.
    Net PnL = gross - fees > 0.
    """
    price_buy = Decimal("50000")
    price_sell = Decimal("50150")  # 0.30% gross spread
    gross_pnl = (price_sell - price_buy) * size  # 75 USDT
    # VIP maker fee: 0.03% per leg × 2 legs
    fee = (price_buy + price_sell) / 2 * size * Decimal("0.0003") * 2  # ~15 USDT
    return {
        "size": size,
        "buy_price": price_buy,
        "sell_price": price_sell,
        "gross_pnl": gross_pnl,
        "fee": fee,
        "net_pnl": gross_pnl - fee,
        "is_win": True,
    }


def make_losing_trade(size: Decimal = Decimal("0.5")) -> dict:
    """Simulate a losing trade (adverse slippage after leg 1 fill).

    Loss is bounded: adverse move + fees, typical arb rollback scenario.
    """
    price_buy = Decimal("50000")
    price_sell = Decimal("49965")  # -0.07% adverse
    gross_pnl = (price_sell - price_buy) * size  # -17.5 USDT
    fee = (price_buy + price_sell) / 2 * size * Decimal("0.0003") * 2  # ~15 USDT
    return {
        "size": size,
        "buy_price": price_buy,
        "sell_price": price_sell,
        "gross_pnl": gross_pnl,
        "fee": fee,
        "net_pnl": gross_pnl - fee,
        "is_win": False,
    }
