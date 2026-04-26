"""PositionManagerListener — Phase 5.2.4 listener #14 (HIGH risk, 2026-04-26).

PositionManager open/close 동기 인덱스 + 비동기 PM queue dispatch.
원본: engine/src/runtime/risk_execution.py:549-593

⚠️ NOT idempotent in queue but idempotent via PositionManager WAL dedup.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PositionManagerListener:
    """Single-responsibility: PositionManager open/close dispatch (sync index + async queue).

    DI:
    - position_manager: PositionManager (또는 None)
    - pm_queue: asyncio.Queue (bounded, ordered ops)
    """

    name = "position_manager"

    def __init__(self, position_manager: Any, pm_queue: Any) -> None:
        self._pm = position_manager
        self._queue = pm_queue

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._pm is None:
            return
        status_val = getattr(getattr(result, "status", None), "value",
                             str(getattr(result, "status", "")))
        if status_val != "success":
            return
        try:
            legs_info = [
                (getattr(leg, "trade", None), getattr(leg, "order", None))
                for leg in getattr(result, "legs", [])
            ]
            _is_close_exec = any(
                isinstance(getattr(o, "metadata", None), dict) and (
                    o.metadata.get("reduceOnly") is True
                    or str(o.metadata.get("leg_type", "")).startswith(
                        ("settlement_close", "timeout_close")
                    )
                )
                for _, o in legs_info if o
            )
            for trade, order in legs_info:
                if trade is None or order is None:
                    continue
                _side_str = getattr(order.side, "value", str(order.side)).upper()
                if _is_close_exec:
                    _op_kwargs = ("close_position", {
                        "strategy_id": request.strategy_id,
                        "exchange_id": order.exchange_id,
                        "symbol": order.symbol,
                        "close_price": trade.price,
                    })
                else:
                    _op_kwargs = ("open_position", {
                        "strategy_id": request.strategy_id,
                        "exchange_id": order.exchange_id,
                        "symbol": order.symbol,
                        "side": "LONG" if _side_str == "BUY" else "SHORT",
                        "quantity": trade.amount,
                        "entry_price": trade.price,
                    })
                # WS-4 Step 2: sync 인덱스 먼저
                try:
                    self._pm.update_index_sync(_op_kwargs[0], **_op_kwargs[1])
                except Exception as _sync_err:
                    logger.debug("update_index_sync_failed: %s", _sync_err)
                # async queue dispatch
                if self._queue is not None:
                    try:
                        self._queue.put_nowait(_op_kwargs)
                    except asyncio.QueueFull:
                        logger.warning(
                            "pm_queue_full — fallback ensure_future op=%s sym=%s",
                            _op_kwargs[0], _op_kwargs[1].get("symbol"),
                        )
                        _op_name, _op_args = _op_kwargs
                        asyncio.ensure_future(getattr(self._pm, _op_name)(**_op_args))
        except Exception as exc:
            logger.debug("position_manager.error %s", exc)
