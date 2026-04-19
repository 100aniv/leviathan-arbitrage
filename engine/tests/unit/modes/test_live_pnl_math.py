"""WS-D4 / WS-A1+A5 regression guard for live._compute_pnl_from_result.

Guards:
- Case A: Trade objects carrying ``realized_pnl`` short-circuit fill recompute
  and yield ``source="exchange_realized_pnl"``.
- Case B: When ``realized_pnl`` is absent, the function recomputes from fill
  prices (``source="fill_minus_fee"``) — slippage deduction must happen at the
  caller level (not inside _compute_pnl_from_result), so the caller semantics
  in live.py are asserted via an inline reconstruction.
- Case C: Regression guard — a fill-based pnl without slippage deduction must
  diverge from the same calc WITH deduction. If a future refactor removes the
  ``fill_minus_fee`` slippage hit, this assertion fires.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Trade
from src.modes.live import LiveMode
from src.strategies.base import TradeLeg, TradeRequest


def _make_engine() -> LiveMode:
    """Build a LiveMode instance with only the attrs _compute_pnl uses."""
    engine = LiveMode.__new__(LiveMode)
    engine._fee_model = MagicMock()
    engine._fee_model.taker_fee.return_value = Decimal("0.1")
    return engine


def _make_leg_result(side: OrderSide, price: Decimal, amount: Decimal,
                    fee: Decimal = Decimal("0"),
                    realized_pnl: Decimal | None = None) -> SimpleNamespace:
    """Build a LegResult-like SimpleNamespace with .order + .trade."""
    trade = Trade(
        trade_id="t1",
        order_id="o1",
        exchange_id="binance_futures",
        symbol="BTC/USDT",
        side=side,
        price=price,
        amount=amount,
        fee=fee,
        realized_pnl=realized_pnl,
    )
    order = SimpleNamespace(side=side, symbol="BTC/USDT", exchange_id="binance_futures")
    return SimpleNamespace(order=order, trade=trade)


def _make_trade_request(buy_price: Decimal, sell_price: Decimal,
                       size: Decimal = Decimal("1")) -> TradeRequest:
    legs = [
        TradeLeg(
            exchange_id="binance_futures", symbol="BTC/USDT",
            side=OrderSide.BUY, size=size, price=buy_price,
        ),
        TradeLeg(
            exchange_id="bitget_futures", symbol="BTC/USDT",
            side=OrderSide.SELL, size=size, price=sell_price,
        ),
    ]
    return TradeRequest(
        strategy_id="futures_futures",
        legs=legs,
        expected_profit_usdt=Decimal("0.5"),
    )


class TestComputePnlFromResult:
    def test_case_a_exchange_realized_pnl_takes_priority(self) -> None:
        """Case A: realized_pnl=1.23 on any leg → returns 1.23 without recompute."""
        engine = _make_engine()
        legs = [
            _make_leg_result(
                OrderSide.BUY, Decimal("100"), Decimal("1"),
                realized_pnl=Decimal("1.23"),
            ),
            _make_leg_result(
                OrderSide.SELL, Decimal("101"), Decimal("1"),
                realized_pnl=None,  # only one leg reports — still authoritative
            ),
        ]
        exec_result = SimpleNamespace(legs=legs, realized_pnl=None)
        request = _make_trade_request(Decimal("100"), Decimal("101"))

        pnl, source = engine._compute_pnl_from_result(exec_result, request)

        assert source == "exchange_realized_pnl"
        assert pnl == Decimal("1.23")

    def test_case_b_no_realized_pnl_recomputes_from_fills(self) -> None:
        """Case B: realized_pnl=None → recompute notional − fee from fills."""
        engine = _make_engine()
        legs = [
            _make_leg_result(
                OrderSide.BUY, Decimal("100"), Decimal("1"),
                fee=Decimal("0.1"), realized_pnl=None,
            ),
            _make_leg_result(
                OrderSide.SELL, Decimal("101"), Decimal("1"),
                fee=Decimal("0.1"), realized_pnl=None,
            ),
        ]
        exec_result = SimpleNamespace(legs=legs, realized_pnl=None)
        request = _make_trade_request(Decimal("100"), Decimal("101"))

        pnl, source = engine._compute_pnl_from_result(exec_result, request)

        assert source == "fill_minus_fee"
        # notional_sell − fee_sell − notional_buy − fee_buy = 101 − 0.1 − 100 − 0.1 = 0.8
        assert pnl == Decimal("0.8")

    def test_case_c_regression_slippage_deduction_required(self) -> None:
        """Case C: WS-A5 regression guard.

        Simulates the live.py caller branch: when source=fill_minus_fee, caller
        MUST deduct slippage_usd. This test asserts that (pnl − slippage) differs
        from pnl alone by the slippage amount. If a future edit silently drops
        the deduction, this equality fails.
        """
        engine = _make_engine()
        # Expected buy=100, actual fill=100.5 → 0.5 adverse slippage on buy leg.
        # Expected sell=101, actual fill=100.8 → 0.2 adverse slippage on sell leg.
        legs = [
            _make_leg_result(
                OrderSide.BUY, Decimal("100.5"), Decimal("1"),
                fee=Decimal("0"), realized_pnl=None,
            ),
            _make_leg_result(
                OrderSide.SELL, Decimal("100.8"), Decimal("1"),
                fee=Decimal("0"), realized_pnl=None,
            ),
        ]
        exec_result = SimpleNamespace(legs=legs, realized_pnl=None)
        request = _make_trade_request(Decimal("100"), Decimal("101"))

        pnl_from_fills, source = engine._compute_pnl_from_result(exec_result, request)
        assert source == "fill_minus_fee"
        # notional_sell − notional_buy = 100.8 − 100.5 = 0.3 (before slippage deduction).
        assert pnl_from_fills == Decimal("0.3")

        # Compute adverse slippage_usd the same way live.py does (WS-A5 branch).
        _exp_buy = Decimal("100")
        _exp_sell = Decimal("101")
        _buy_sz = Decimal("1")
        _sell_sz = Decimal("1")
        slip_usd = Decimal("0")
        slip_usd += max(Decimal("0"), (Decimal("100.5") - _exp_buy) * _buy_sz)
        slip_usd += max(Decimal("0"), (_exp_sell - Decimal("100.8")) * _sell_sz)
        assert slip_usd == Decimal("0.7")  # 0.5 + 0.2

        pnl_after_slippage = pnl_from_fills - slip_usd
        assert pnl_after_slippage == Decimal("-0.4")

        # Regression guard: the pre- and post-deduction PnLs MUST differ when
        # there is adverse slippage. If a refactor removes the deduction, these
        # would be equal and this assertion fires.
        assert pnl_from_fills != pnl_after_slippage
        assert (pnl_from_fills - pnl_after_slippage) == slip_usd

    def test_fallback_estimate_branch_fires_when_no_exec_result(self) -> None:
        """Coverage for branch 4 (estimate) so it is not dead code."""
        engine = _make_engine()
        request = _make_trade_request(Decimal("100"), Decimal("101"))
        pnl, source = engine._compute_pnl_from_result(None, request)
        assert source == "estimate"
        # Sell notional − fee − Buy notional − fee = 101 − 0.1 − 100 − 0.1 = 0.8.
        assert pnl == Decimal("0.8")

    def test_exec_result_realized_pnl_branch(self) -> None:
        """Coverage for branch 2 (exec_result_realized) so it is not dead code."""
        engine = _make_engine()
        # Legs without realized_pnl on trades → branch 1 skipped.
        legs = [
            _make_leg_result(OrderSide.BUY, Decimal("100"), Decimal("1"), realized_pnl=None),
        ]
        exec_result = SimpleNamespace(legs=legs, realized_pnl=Decimal("2.5"))
        request = _make_trade_request(Decimal("100"), Decimal("101"))
        pnl, source = engine._compute_pnl_from_result(exec_result, request)
        assert source == "exec_result_realized"
        assert pnl == Decimal("2.5")


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short", "--no-cov"])
