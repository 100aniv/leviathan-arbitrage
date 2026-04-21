"""Shared fixtures for Day 11 parallel-leg tests.

Keeps test modules small by centralising the fake adapter + monkeypatch
helper used by every test_parallel_legs_*.py file.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.execution.cross_exchange_v2 import (
    FLAG_ENV_VAR as PARALLEL_FLAG_ENV_VAR,
)
from src.execution.journal import FLAG_ENV_VAR as JOURNAL_FLAG_ENV_VAR
from src.execution.journal import ExecutionJournal
from src.execution.order_state import FLAG_ENV_VAR as STATE_MACHINE_FLAG_ENV_VAR
from src.execution.order_state import OrderStateMachine
from src.execution.router import FLAG_ENV_VAR as ROUTER_FLAG_ENV_VAR


def enable_all_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn on all four feature flags gating Day 11."""
    monkeypatch.setenv(JOURNAL_FLAG_ENV_VAR, "true")
    monkeypatch.setenv(STATE_MACHINE_FLAG_ENV_VAR, "true")
    monkeypatch.setenv(ROUTER_FLAG_ENV_VAR, "true")
    monkeypatch.setenv(PARALLEL_FLAG_ENV_VAR, "true")


async def make_state_machine(tmp_path: Path) -> tuple[OrderStateMachine, ExecutionJournal]:
    """C-1: build a started journal + state-machine pair for parallel-leg tests.

    After the C-1 fix, ``CrossExchangeV2Executor`` rejects ``state_machine=None``
    when the parallel-legs flag is on. Tests call this helper to satisfy the
    new invariant. Caller is responsible for awaiting ``journal.stop()`` in a
    ``finally`` block.
    """
    journal = ExecutionJournal(db_path=tmp_path / "parallel_legs_test.db")
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    return sm, journal


@dataclass
class FakeIOCResult:
    filled_size: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    order_id: str = "EX-1"


@dataclass
class FakeMarketResult:
    filled_size: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    order_id: str = "EX-2"


class FakeAdapter:
    """Minimal adapter double for parallel-leg tests.

    ``place_ioc_limit`` and ``place_market`` record every call so tests can
    assert on the topology.
    """

    def __init__(
        self,
        *,
        ioc_result: FakeIOCResult | None = None,
        market_result: FakeMarketResult | None = None,
        raises_on_ioc: bool = False,
        raises_on_market: bool = False,
    ) -> None:
        self._ioc = ioc_result or FakeIOCResult()
        self._market = market_result or FakeMarketResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("30100"),
        )
        self._raises_on_ioc = raises_on_ioc
        self._raises_on_market = raises_on_market
        self.ioc_calls: list[dict[str, Any]] = []
        self.market_calls: list[dict[str, Any]] = []

    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> FakeIOCResult:
        self.ioc_calls.append(
            {"symbol": symbol, "side": side, "price": price, "size": size}
        )
        if self._raises_on_ioc:
            raise RuntimeError("simulated_ioc_failure")
        return self._ioc

    async def place_market(
        self, symbol: str, side: str, size: Decimal
    ) -> FakeMarketResult:
        self.market_calls.append(
            {"symbol": symbol, "side": side, "size": size}
        )
        if self._raises_on_market:
            raise RuntimeError("simulated_market_failure")
        return self._market


def make_trade_request(
    *,
    size: Decimal = Decimal("1.0"),
    buy_exchange: str = "binance",
    sell_exchange: str = "bybit",
    symbol: str = "BTCUSDT",
    buy_price: Decimal = Decimal("30000"),
    sell_price: Decimal = Decimal("30050"),
    trace_id: str | None = None,
) -> SimpleNamespace:
    """Construct a minimal TradeRequest-shaped object for the executor.

    SimpleNamespace keeps tests independent of pydantic validation.
    """
    leg_a = SimpleNamespace(
        exchange_id=buy_exchange,
        symbol=symbol,
        side="buy",
        size=size,
        price=buy_price,
    )
    leg_b = SimpleNamespace(
        exchange_id=sell_exchange,
        symbol=symbol,
        side="sell",
        size=size,
        price=sell_price,
    )
    return SimpleNamespace(
        legs=[leg_a, leg_b],
        trace_id=trace_id,
        strategy_id="cross_exchange",
    )
