"""MarketRecorderListener — Phase 5.2.4 listener #2 (2026-04-26).

ExecutionResult → TimescaleDB record_execution. dashboard + WFA backtest input.

원본: engine/src/runtime/risk_execution.py:656-692

설계:
- success status only
- BUY/SELL leg pair에서 prices 추출 (실 fill price 우선)
- mode 결정: engine._live_mode._execution_mode 또는 "live" fallback
- recorder None이면 silent skip (paper-only 시나리오)
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.listeners._helpers import get_side, is_status_success

logger = logging.getLogger(__name__)


class MarketRecorderListener:
    """Single-responsibility: TimescaleDB execution record.

    Dependencies:
    - market_recorder: MarketRecorder instance (또는 None)
    - live_mode (optional): execution_mode 추출용 reference

    Idempotency: depends on MarketRecorder.record_execution 구현.
    DB-side dedup OK 가정.
    """

    name = "market_recorder"

    def __init__(self, market_recorder: Any, live_mode: Any = None) -> None:
        self._recorder = market_recorder
        self._live_mode = live_mode

    def on_execution_result(self, request: Any, result: Any) -> None:
        """Build dict + call record_execution. Skip if recorder=None or no legs."""
        try:
            if not is_status_success(result):
                return
            if self._recorder is None or not request.legs:
                return

            from src.core.models import OrderSide as _OS
            _buy_legs = [l for l in request.legs if l.side == _OS.BUY]
            _sell_legs = [l for l in request.legs if l.side == _OS.SELL]
            if not _buy_legs or not _sell_legs:
                return

            _bp = _buy_legs[0].price or Decimal("0")
            _sp = _sell_legs[0].price or Decimal("0")
            # Prefer actual fill prices
            for _lr in getattr(result, "legs", []):
                _t = getattr(_lr, "trade", None)
                _o = getattr(_lr, "order", None)
                if _t and _o:
                    _s = get_side(_o)
                    if _s == "BUY":
                        _bp = Decimal(str(_t.price))
                    else:
                        _sp = Decimal(str(_t.price))

            _mode = "live"
            if self._live_mode is not None:
                _mode = getattr(self._live_mode, "_execution_mode", "live")

            self._recorder.record_execution(
                strategy_id=request.strategy_id,
                buy_exchange=str(_buy_legs[0].exchange_id),
                sell_exchange=str(_sell_legs[0].exchange_id),
                symbol=request.legs[0].symbol,
                buy_price=_bp,
                sell_price=_sp,
                size=request.legs[0].size,
                net_pnl=Decimal(str(getattr(result, "pnl", 0) or 0)),
                status="filled",
                mode=_mode,
            )
        except Exception as exc:
            logger.debug(
                "db_record_execution_failed strategy=%s err=%s",
                getattr(request, "strategy_id", "unknown"), exc,
            )
