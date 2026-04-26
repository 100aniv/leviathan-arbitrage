"""Phase 5.1: ExchangeAdapterPort runtime_checkable Protocol 검증."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.ports.exchange_adapter_port import ExchangeAdapterPort


class _MockAdapter:
    exchange_id = "mock_test"

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def place_order(self, order: Any) -> Any:
        return None

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        return True

    def supports_symbol(self, symbol: str) -> bool:
        return True

    def get_min_notional(self, symbol: str) -> Decimal:
        return Decimal("0")

    @property
    def _market_type(self) -> str:
        return "spot"

    async def get_balance(self) -> dict[str, Decimal]:
        return {"USDT": Decimal("1000")}

    @property
    def health_score(self) -> Decimal:
        return Decimal("1")


class TestExchangeAdapterPortProtocol:
    def test_mock_adapter_implements_port(self) -> None:
        adapter = _MockAdapter()
        assert isinstance(adapter, ExchangeAdapterPort)

    def test_paper_exchange_adapter_has_required_methods(self) -> None:
        from src.execution.paper_adapter import PaperExchangeAdapter
        for attr in ("connect", "place_order", "cancel_order",
                     "supports_symbol", "get_min_notional"):
            assert hasattr(PaperExchangeAdapter, attr), f"missing {attr}"

    def test_port_public_methods_count(self) -> None:
        port_methods = [a for a in dir(ExchangeAdapterPort) if not a.startswith("_")]
        for method in ("place_order", "cancel_order", "supports_symbol",
                       "get_min_notional", "get_balance", "connect", "disconnect"):
            assert method in port_methods
