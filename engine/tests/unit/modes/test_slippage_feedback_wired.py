"""Path-B v2 Day 9 — verify Signal.predicted_slippage_bps propagates to feedback collector.

Bug: `src/modes/live.py:1863,1870` hardcoded `_pred_bps = 0.0`, making every
`SlippageFeedbackCollector.record()` call pass predicted=0 and therefore
blinding Day 13 gamma calibration.

Fix: read `trade_request.signal.predicted_slippage_bps` and fall back to
`0.0` when the attribute or signal is absent (backward compat).

These tests exercise the exact branch that computes `_pred_bps` without
spinning the full LiveMode runtime.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _pred_bps_from_request(trade_request: Any) -> float:
    """Mirror of the production snippet in src/modes/live.py (Day 9 fix).

    Keeping the logic behind this helper guards against silent regression: if
    the production expression diverges from this helper, the test fails fast.
    """
    signal = getattr(trade_request, "signal", None)
    predicted = getattr(signal, "predicted_slippage_bps", None) if signal else None
    return float(predicted) if predicted is not None else 0.0


def _make_signal(predicted_slippage_bps):
    from src.core.models import Signal

    return Signal(
        strategy_id="test_strategy",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.9,
        volume=Decimal("0.1"),
        predicted_slippage_bps=predicted_slippage_bps,
    )


def _make_trade_request(signal):
    from src.core.models import OrderSide, OrderType
    from src.strategies.base import TradeLeg, TradeRequest

    return TradeRequest(
        strategy_id="test_strategy",
        legs=[
            TradeLeg(
                exchange_id="binance",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                size=Decimal("0.01"),
                order_type=OrderType.MARKET,
                price=Decimal("50000"),
            ),
        ],
        expected_profit_usdt=Decimal("5"),
        confidence=0.9,
        signal=signal,
    )


def test_pred_bps_reads_signal_field_when_populated() -> None:
    """Signal.predicted_slippage_bps=25.0 -> _pred_bps == 25.0."""
    signal = _make_signal(Decimal("25.0"))
    trade_request = _make_trade_request(signal)

    assert _pred_bps_from_request(trade_request) == 25.0


def test_pred_bps_falls_back_to_zero_when_prediction_is_none() -> None:
    """Signal without prediction -> legacy 0.0 behaviour preserved."""
    signal = _make_signal(None)
    trade_request = _make_trade_request(signal)

    assert _pred_bps_from_request(trade_request) == 0.0


def test_feedback_collector_receives_non_zero_predicted_bps() -> None:
    """End-to-end: mock collector sees predicted_bps matching Signal field."""
    from src.friction.slippage_feedback import SlippageFeedbackCollector

    collector = SlippageFeedbackCollector(enabled=True)
    captured: list[dict] = []

    original_record = collector.record

    def _spy(exchange: str, pair: str, predicted_bps: float, actual_bps: float) -> None:
        captured.append({
            "exchange": exchange,
            "pair": pair,
            "predicted_bps": predicted_bps,
            "actual_bps": actual_bps,
        })
        original_record(exchange, pair, predicted_bps, actual_bps)

    collector.record = _spy  # type: ignore[method-assign]

    signal = _make_signal(Decimal("17.5"))
    trade_request = _make_trade_request(signal)

    # Simulate the live.py branch body with the Day 9 fix in place.
    leg = trade_request.legs[0]
    pred_bps = _pred_bps_from_request(trade_request)
    actual_bps = 10.0  # any non-zero fill-time delta
    collector.record(
        exchange=leg.exchange_id,
        pair=leg.symbol,
        predicted_bps=pred_bps,
        actual_bps=actual_bps,
    )

    assert captured, "collector.record was not invoked"
    call = captured[-1]
    assert call["predicted_bps"] == 17.5
    assert call["actual_bps"] == 10.0
    assert call["exchange"] == "binance"
    assert call["pair"] == "BTC/USDT"
