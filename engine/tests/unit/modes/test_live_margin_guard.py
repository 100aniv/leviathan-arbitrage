"""BUG-74: Margin guard unit tests.

Verifies that new ENTRY trades on margin-exhausted futures exchanges are
blocked before reaching the executor, preventing -2019 retry loops.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helper — minimal trade-request-like objects
# ---------------------------------------------------------------------------


def _make_leg(exchange_id: str, reduce_only: bool = False):
    leg = MagicMock()
    leg.exchange_id = exchange_id
    leg.symbol = "TEST/USDT"
    leg.reduce_only = reduce_only
    return leg


def _make_trade_request(legs, strategy_id: str = "futures_futures_v1"):
    req = MagicMock()
    req.legs = legs
    req.strategy_id = strategy_id
    return req


# ---------------------------------------------------------------------------
# Logic extracted from live.py BUG-74 block (lines 1118-1129)
# — tests the guard logic directly without instantiating LiveMode
# ---------------------------------------------------------------------------


MIN_MARGIN_ENTRY_USD = 3.0


def _margin_guard(trade_request, cached_margin: dict, is_close_req: bool) -> bool:
    """Returns True if the trade should be BLOCKED by the margin guard."""
    if is_close_req:
        return False
    for leg in trade_request.legs:
        if leg.exchange_id and "futures" in leg.exchange_id:
            cached = float(cached_margin.get(leg.exchange_id, float("inf")))
            if cached < MIN_MARGIN_ENTRY_USD:
                return True
    return False


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestMarginGuardLogic:
    def test_entry_blocked_when_margin_below_threshold(self):
        """Entry trade on futures exchange with $1.50 margin must be blocked."""
        legs = [_make_leg("binance_futures"), _make_leg("bitget_futures")]
        req = _make_trade_request(legs)
        cached = {"binance_futures": Decimal("1.50"), "bitget_futures": Decimal("10.00")}

        assert _margin_guard(req, cached, is_close_req=False) is True

    def test_entry_allowed_when_margin_above_threshold(self):
        """Entry trade with $5.00 margin must NOT be blocked."""
        legs = [_make_leg("binance_futures"), _make_leg("bitget_futures")]
        req = _make_trade_request(legs)
        cached = {"binance_futures": Decimal("5.00"), "bitget_futures": Decimal("10.00")}

        assert _margin_guard(req, cached, is_close_req=False) is False

    def test_entry_allowed_when_margin_exactly_at_threshold(self):
        """Entry trade with exactly $3.00 margin must NOT be blocked (>= 3.0 is OK)."""
        legs = [_make_leg("binance_futures")]
        req = _make_trade_request(legs)
        cached = {"binance_futures": Decimal("3.00")}

        assert _margin_guard(req, cached, is_close_req=False) is False

    def test_close_request_exempt_even_with_zero_margin(self):
        """Reduce-only (exit) trade must bypass guard regardless of margin."""
        legs = [_make_leg("binance_futures", reduce_only=True)]
        req = _make_trade_request(legs)
        cached = {"binance_futures": Decimal("0.00")}

        assert _margin_guard(req, cached, is_close_req=True) is False

    def test_spot_exchange_not_affected(self):
        """Guard only applies to futures exchanges; spot trade is never blocked."""
        legs = [_make_leg("binance"), _make_leg("bitget")]
        req = _make_trade_request(legs)
        # No futures margin data
        cached = {}

        assert _margin_guard(req, cached, is_close_req=False) is False

    def test_missing_cache_entry_defaults_to_inf(self):
        """Unknown exchange (not in cached_margin) defaults to inf → not blocked."""
        legs = [_make_leg("new_futures_exchange")]
        req = _make_trade_request(legs)
        cached = {}  # nothing cached

        assert _margin_guard(req, cached, is_close_req=False) is False

    def test_second_leg_low_margin_triggers_block(self):
        """Even if first leg has enough margin, low margin on second leg blocks."""
        legs = [_make_leg("binance_futures"), _make_leg("bitget_futures")]
        req = _make_trade_request(legs)
        cached = {"binance_futures": Decimal("10.00"), "bitget_futures": Decimal("0.50")}

        assert _margin_guard(req, cached, is_close_req=False) is True


# ---------------------------------------------------------------------------
# Source-code inspection: verify guard is present in live.py
# ---------------------------------------------------------------------------


class TestMarginGuardSourcePresence:
    """Path-B Day-2: BUG-74 margin guard migrated from live.py to PreTradeValidator.
    These tests now validate presence in pre_trade_validator.py OR live.py
    (fallback for partial rollbacks). The guard must exist somewhere in the
    pre-trade gate pipeline — location is an implementation detail.
    """

    @staticmethod
    def _get_gate_source() -> str:
        """Return concatenated source of live.py + pre_trade_validator.py."""
        import pathlib
        eng = pathlib.Path(__file__).parent.parent.parent.parent
        live_src = (eng / "src/modes/live.py").read_text()
        try:
            ptv_src = (eng / "src/execution/pre_trade_validator.py").read_text()
        except FileNotFoundError:
            ptv_src = ""
        return live_src + "\n" + ptv_src

    def test_bug74_guard_present_in_gate_pipeline(self):
        """BUG-74 guard must exist in either live.py or pre_trade_validator.py.
        After Path-B Day-2 extraction the guard uses typed ReasonCode.MARGIN_INSUFFICIENT
        instead of the legacy entry_blocked_margin_low log key.
        """
        source = self._get_gate_source()
        assert "BUG-74" in source, "BUG-74 guard comment not found anywhere"
        assert "MIN_MARGIN_ENTRY_USD" in source, (
            "MIN_MARGIN_ENTRY_USD constant not found"
        )
        # legacy log key OR post-extraction reason-code either satisfies
        assert (
            "entry_blocked_margin_low" in source
            or "MARGIN_INSUFFICIENT" in source
        ), "Margin-insufficient gate emission not found"

    def test_close_request_exemption_is_first_check(self):
        """Reduce-only exemption must appear BEFORE the per-leg margin check
        inside the gate-owning function.

        Path-B Day-2: guard moved to PreTradeValidator._check_margin_guard.
        We locate that method and verify the close-exemption precedes the
        margin-loop within the method body.
        """
        import pathlib
        eng = pathlib.Path(__file__).parent.parent.parent.parent
        ptv = (eng / "src/execution/pre_trade_validator.py").read_text() \
            if (eng / "src/execution/pre_trade_validator.py").exists() else ""

        if ptv and "BUG-74" in ptv:
            # Post-extraction: test the validator method body.
            import re
            match = re.search(
                r"def _check_margin_guard\(.*?\)\s*->.*?:(.*?)(?=\n    [a-zA-Z_]|\nclass |\Z)",
                ptv, re.DOTALL,
            )
            assert match is not None, "_check_margin_guard method not found"
            body = match.group(1)
            idx_close = body.find("if is_close")
            idx_margin = body.find("MIN_MARGIN_ENTRY_USD")
            assert idx_close != -1, "is_close exemption not found in guard method"
            assert idx_margin != -1, "MIN_MARGIN_ENTRY_USD not found in guard method"
            assert idx_close < idx_margin, (
                "Close-request exemption must precede margin loop inside the guard method"
            )
        else:
            # Pre-extraction fallback: ordering within live.py.
            src = (eng / "src/modes/live.py").read_text()
            idx_close = src.find("if not _is_close_req")
            idx_margin = src.find("self._MIN_MARGIN_ENTRY_USD")
            assert idx_close != -1 and idx_margin != -1
            assert idx_close < idx_margin
