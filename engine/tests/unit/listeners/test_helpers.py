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
    extract_legs_info,
    get_side,
    is_close_execution,
    is_close_leg,
    is_status_success,
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
