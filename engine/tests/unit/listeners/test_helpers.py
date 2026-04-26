"""Codex SUGGEST (2026-04-26) listener helper functions 검증.

src/listeners/_helpers.py:
- extract_legs_info(result) → [(trade, order), ...]
- is_close_leg(leg_or_order) → bool
- is_close_execution(legs_info) → bool
- get_side(order) → "BUY"/"SELL"/""
- is_status_success(result) → bool
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.listeners._helpers import (
    effective_pnl,
    extract_legs_info,
    get_side,
    get_status_value,
    is_close_execution,
    is_close_leg,
    is_status_success,
    request_to_summary,
)


class TestExtractLegsInfo:
    def test_empty_result(self) -> None:
        result = SimpleNamespace(legs=[])
        assert extract_legs_info(result) == []

    def test_no_legs_attr(self) -> None:
        result = SimpleNamespace()
        assert extract_legs_info(result) == []

    def test_returns_tuples(self) -> None:
        leg1 = SimpleNamespace(trade="trade1", order="order1")
        leg2 = SimpleNamespace(trade="trade2", order="order2")
        result = SimpleNamespace(legs=[leg1, leg2])
        assert extract_legs_info(result) == [("trade1", "order1"), ("trade2", "order2")]

    def test_handles_missing_attrs(self) -> None:
        leg = SimpleNamespace()  # no trade, no order
        result = SimpleNamespace(legs=[leg])
        assert extract_legs_info(result) == [(None, None)]


class TestIsCloseLeg:
    def test_reduce_only_true(self) -> None:
        order = SimpleNamespace(metadata={"reduceOnly": True})
        assert is_close_leg(order) is True

    def test_settlement_close_leg_type(self) -> None:
        order = SimpleNamespace(metadata={"leg_type": "settlement_close"})
        assert is_close_leg(order) is True

    def test_timeout_close_leg_type(self) -> None:
        order = SimpleNamespace(metadata={"leg_type": "timeout_close_30s"})
        assert is_close_leg(order) is True

    def test_spread_exit_leg_type(self) -> None:
        order = SimpleNamespace(metadata={"leg_type": "spread_exit_funding"})
        assert is_close_leg(order) is True

    def test_normal_open_leg(self) -> None:
        order = SimpleNamespace(metadata={"leg_type": "open_entry"})
        assert is_close_leg(order) is False

    def test_no_metadata(self) -> None:
        order = SimpleNamespace()
        assert is_close_leg(order) is False

    def test_metadata_not_dict(self) -> None:
        order = SimpleNamespace(metadata="invalid")
        assert is_close_leg(order) is False


class TestIsCloseExecution:
    def test_any_close_returns_true(self) -> None:
        legs_info = [
            (None, SimpleNamespace(metadata={"leg_type": "open_entry"})),
            (None, SimpleNamespace(metadata={"reduceOnly": True})),
        ]
        assert is_close_execution(legs_info) is True

    def test_no_close_returns_false(self) -> None:
        legs_info = [
            (None, SimpleNamespace(metadata={"leg_type": "open_entry"})),
            (None, SimpleNamespace(metadata={})),
        ]
        assert is_close_execution(legs_info) is False

    def test_empty_returns_false(self) -> None:
        assert is_close_execution([]) is False


class TestGetSide:
    def test_enum_value(self) -> None:
        order = SimpleNamespace(side=SimpleNamespace(value="buy"))
        assert get_side(order) == "BUY"

    def test_string_side(self) -> None:
        order = SimpleNamespace(side="sell")
        assert get_side(order) == "SELL"

    def test_no_side(self) -> None:
        order = SimpleNamespace()
        assert get_side(order) == ""

    def test_none_side(self) -> None:
        order = SimpleNamespace(side=None)
        assert get_side(order) == ""


class TestIsStatusSuccess:
    def test_success(self) -> None:
        result = SimpleNamespace(status=SimpleNamespace(value="success"))
        assert is_status_success(result) is True

    def test_failure(self) -> None:
        result = SimpleNamespace(status=SimpleNamespace(value="failure"))
        assert is_status_success(result) is False

    def test_no_status(self) -> None:
        result = SimpleNamespace()
        assert is_status_success(result) is False


class TestGetStatusValue:
    def test_returns_value(self) -> None:
        result = SimpleNamespace(status=SimpleNamespace(value="rolled_back"))
        assert get_status_value(result) == "rolled_back"

    def test_no_status_returns_empty(self) -> None:
        assert get_status_value(SimpleNamespace()) == ""

    def test_string_status(self) -> None:
        result = SimpleNamespace(status="success")
        assert get_status_value(result) == "success"


class TestEffectivePnL:
    def test_uses_result_pnl_when_set(self) -> None:
        request = SimpleNamespace(expected_profit_usdt=Decimal("0.5"))
        result = SimpleNamespace(pnl=Decimal("3.7"))
        assert effective_pnl(request, result) == 3.7

    def test_falls_back_to_request_when_pnl_none(self) -> None:
        request = SimpleNamespace(expected_profit_usdt=Decimal("0.5"))
        result = SimpleNamespace(pnl=None)
        assert effective_pnl(request, result) == 0.5

    def test_zero_when_no_attrs(self) -> None:
        assert effective_pnl(SimpleNamespace(), SimpleNamespace()) == 0.0


class TestRequestToSummary:
    def test_empty_legs(self) -> None:
        request = SimpleNamespace(strategy_id="x", legs=[], expected_profit_usdt=Decimal("0"))
        result = SimpleNamespace(pnl=Decimal("0"))
        summary = request_to_summary(request, result)
        assert summary["strategy_id"] == "x"
        assert summary["symbol"] == "UNKNOWN"
        assert summary["buy_exchange"] == ""
        assert summary["sell_exchange"] == ""

    def test_buy_sell_legs(self) -> None:
        legs = [
            SimpleNamespace(symbol="BTC/USDT", exchange_id="binance",
                            side=SimpleNamespace(value="buy"),
                            size=Decimal("0.1")),
            SimpleNamespace(symbol="BTC/USDT", exchange_id="okx",
                            side=SimpleNamespace(value="sell"),
                            size=Decimal("0.1")),
        ]
        request = SimpleNamespace(strategy_id="cross", legs=legs,
                                   expected_profit_usdt=Decimal("0"))
        result = SimpleNamespace(pnl=Decimal("1.5"))
        summary = request_to_summary(request, result)
        assert summary["buy_exchange"] == "binance"
        assert summary["sell_exchange"] == "okx"
        assert summary["pnl"] == 1.5
        assert summary["size"] == 0.1
