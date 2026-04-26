"""Listener shared helpers (Codex SUGGEST 2026-04-26).

Cross-listener duplication 제거:
- legs_info 정규화: result.legs → list[(trade, order)] tuples
- close-detection: reduceOnly/settlement_close/timeout_close metadata 인식
- side normalization: SimpleNamespace OrderSide → "BUY"/"SELL" string

Used by CrossHedgeListener / PositionManagerListener / RollbackListener / etc.
DRY principle — single source of truth.
"""
from __future__ import annotations

from typing import Any


_CLOSE_LEG_TYPE_PREFIXES = ("settlement_close", "timeout_close", "spread_exit")


def extract_legs_info(result: Any) -> list[tuple[Any, Any]]:
    """Normalize result.legs → [(trade, order), ...] tuples.

    Returns empty list when result has no legs or legs is iterable-empty.
    Each tuple element may be None (defensive).
    """
    return [
        (getattr(leg, "trade", None), getattr(leg, "order", None))
        for leg in getattr(result, "legs", []) or []
    ]


def is_close_leg(leg_or_order: Any) -> bool:
    """Detect if leg/order is a position-close (reduceOnly or close-prefix leg_type).

    Accepts either:
    - leg (with .order attr) — examines leg.order.metadata
    - order directly (no .order attr) — examines .metadata
    """
    obj = getattr(leg_or_order, "order", leg_or_order)
    metadata = getattr(obj, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    if metadata.get("reduceOnly") is True:
        return True
    leg_type = str(metadata.get("leg_type", ""))
    return leg_type.startswith(_CLOSE_LEG_TYPE_PREFIXES)


def is_close_execution(legs_info: list[tuple[Any, Any]]) -> bool:
    """Returns True if ANY leg in execution is a close (reduceOnly/settlement_close)."""
    return any(is_close_leg(order) for _, order in legs_info if order is not None)


def get_side(order: Any) -> str:
    """Normalize order.side → "BUY" or "SELL" uppercase. Empty string if missing."""
    side = getattr(order, "side", None)
    if side is None:
        return ""
    return getattr(side, "value", str(side)).upper()


def is_status_success(result: Any) -> bool:
    """Check execution_result.status.value == 'success'."""
    status = getattr(result, "status", None)
    if status is None:
        return False
    return getattr(status, "value", str(status)) == "success"
