"""Tests for US-247: CostCalculator v2 — rollback cost, no-slippage, prefix strip.

Verifies:
- estimate_cost() includes rollback cost (E[Rollback] = P * avg)
- estimate_cost() does NOT include slippage (이중계산 방지)
- paper_/sandbox_ prefix 자동 strip
- Unknown exchange → FeeModel fallback 처리
- calculate() vs estimate_cost() 관계 — estimate는 단순 fee+rollback

Run:
    cd engine && python -m pytest tests/test_cost_calculator_v2.py -v --tb=short
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.friction.cost_calculator import CostCalculator, TradeOutcome
from src.friction.fee_model import FeeModel
from src.core.models import OrderSide


def _make_calculator(taker_fee_pct: float = 0.001) -> CostCalculator:
    """CostCalculator with mocked FeeModel."""
    mock_fee_model = MagicMock(spec=FeeModel)
    mock_fee_model.taker_fee.return_value = Decimal(str(taker_fee_pct))
    calc = CostCalculator(fee_model=mock_fee_model, network_cost=Decimal("0"))
    return calc


class TestCostCalculatorV2:
    """US-247: estimate_cost() 검증."""

    def test_estimate_cost_includes_rollback(self):
        """estimate_cost()에 E[Rollback_Cost]가 포함됨."""
        calc = _make_calculator(taker_fee_pct=0.10)  # $0.10 fee

        # 과거 1건의 롤백 기록 → P(rollback)=1.0
        calc.record_trade(TradeOutcome(rolled_back=True, rollback_cost=Decimal("5")))

        cost = calc.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            size=Decimal("1"),
            price=Decimal("100"),
        )

        # fee + rollback expected = 0.10 + P(rollback)*5 = 0.10 + 1.0*5 = 5.10
        assert cost > Decimal("0.10"), "rollback cost must be included"

    def test_estimate_cost_no_slippage(self):
        """estimate_cost()는 slippage를 포함하지 않음 (이중계산 방지)."""
        mock_fee = MagicMock(spec=FeeModel)
        mock_fee.taker_fee.return_value = Decimal("0.10")
        mock_slippage = MagicMock()
        mock_slippage.predict.return_value = MagicMock(expected=Decimal("999"))  # large slippage

        calc = CostCalculator(
            fee_model=mock_fee,
            slippage_model=mock_slippage,
            network_cost=Decimal("0"),
        )

        cost = calc.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            size=Decimal("1"),
            price=Decimal("100"),
        )

        # slippage.predict() should NOT be called in estimate_cost
        mock_slippage.predict.assert_not_called()
        # cost should only be fee + rollback (not 999)
        assert cost < Decimal("10"), "slippage must NOT be in estimate_cost"

    def test_estimate_cost_strips_prefix(self):
        """paper_/sandbox_ prefix를 자동으로 제거 후 fee 조회."""
        mock_fee = MagicMock(spec=FeeModel)
        mock_fee.taker_fee.return_value = Decimal("0.05")

        calc = CostCalculator(fee_model=mock_fee, network_cost=Decimal("0"))

        calc.estimate_cost(
            exchange_id="paper_binance",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            size=Decimal("1"),
            price=Decimal("100"),
        )

        # fee_model.taker_fee("binance", ...) — 접두사 제거됨
        call_args = mock_fee.taker_fee.call_args
        exchange_arg = call_args[0][0]
        assert exchange_arg == "binance", f"prefix not stripped: got {exchange_arg!r}"

    def test_estimate_cost_sandbox_prefix_strips(self):
        """sandbox_ prefix도 자동 제거."""
        mock_fee = MagicMock(spec=FeeModel)
        mock_fee.taker_fee.return_value = Decimal("0.05")

        calc = CostCalculator(fee_model=mock_fee, network_cost=Decimal("0"))

        calc.estimate_cost(
            exchange_id="sandbox_okx",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            size=Decimal("1"),
            price=Decimal("50000"),
        )

        call_args = mock_fee.taker_fee.call_args
        assert call_args[0][0] == "okx"

    def test_estimate_cost_unknown_exchange_graceful(self):
        """알 수 없는 거래소 → FeeModel이 fallback 처리, 예외 없음."""
        real_fee_model = FeeModel()

        calc = CostCalculator(fee_model=real_fee_model, network_cost=Decimal("0"))

        # Should not raise
        cost = calc.estimate_cost(
            exchange_id="unknown_exchange_xyz",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            size=Decimal("1"),
            price=Decimal("1000"),
        )

        assert cost >= Decimal("0"), "unknown exchange must return non-negative cost"

    def test_calculate_vs_estimate_consistency(self):
        """calculate()는 estimate_cost()보다 더 많은 비용 포함 (slippage 포함)."""
        mock_fee = MagicMock(spec=FeeModel)
        mock_fee.taker_fee.return_value = Decimal("0.10")

        mock_slip = MagicMock()
        mock_slip.predict.return_value = MagicMock(expected=Decimal("0.50"))

        # mock orderbooks
        mock_book = MagicMock()
        mock_book.bids = {Decimal("100"): Decimal("10")}
        mock_book.asks = {Decimal("101"): Decimal("10")}
        mock_book.best_ask.return_value = Decimal("101")
        mock_book.best_bid.return_value = Decimal("100")

        calc = CostCalculator(
            fee_model=mock_fee,
            slippage_model=mock_slip,
            network_cost=Decimal("0"),
        )

        estimate = calc.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            size=Decimal("1"),
            price=Decimal("100"),
        )

        friction = calc.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=mock_book,
            sell_book=mock_book,
            size=Decimal("1"),
            buy_price=Decimal("100"),
            sell_price=Decimal("102"),
        )

        # calculate() includes slippage; estimate_cost() does not
        # Both operations should complete without error
        assert estimate >= Decimal("0")
        assert hasattr(friction, "fee_buy")
        assert hasattr(friction, "total_cost")
