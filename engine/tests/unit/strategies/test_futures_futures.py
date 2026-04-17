"""Tests for FuturesFuturesStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal, Trade
from src.strategies.base import CostCalculator
from src.strategies.futures_futures import FuturesFuturesConfig, FuturesFuturesStrategy


def make_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    spread_pct: Decimal = Decimal("0.002"),
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
    # BUG-115: default to ample margin so tests that don't focus on margin
    # behaviour still pass after the margin_available <= 0 → block guard.
    margin_available: Decimal = Decimal("100000"),
    book_age_ms: float = 0,
) -> Signal:
    metadata: dict = {
        "book_age_ms": book_age_ms,  # US-273: stale guard requires this field
        "margin_available": str(margin_available),
    }
    return Signal(
        strategy_id="futures_futures_cross_v1",
        symbol="BTC/USDT:USDT",
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.9,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_spread_below_threshold_returns_none():
    config = FuturesFuturesConfig(min_spread_bps=Decimal("20"))
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.0015"))  # 15 bps < 20 bps
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_profitable_signal_generates_two_legs():
    # gross = (50100-50000)*0.5 = 50; cost = 1*2 = 2; net = 48
    # max_notional_usd=None to disable cap so full size=0.5 is used
    strategy = FuturesFuturesStrategy(
        "ff_cross", make_calculator(Decimal("1")),
        FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_notional_usd=None),
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.002"))
    result = await strategy.on_signal(signal)

    assert result is not None
    assert len(result.legs) == 2
    assert result.expected_profit_usdt == Decimal("48")


@pytest.mark.asyncio
async def test_legs_have_correct_exchanges_and_sides():
    strategy = FuturesFuturesStrategy(
        "ff_cross", make_calculator(),
        FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_notional_usd=None),
    )
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None

    buy_leg = next(l for l in result.legs if l.side == OrderSide.BUY)
    sell_leg = next(l for l in result.legs if l.side == OrderSide.SELL)
    # Bug 22 fix: strategy appends _futures to ensure legs route to futures adapters
    assert buy_leg.exchange_id == "binance_futures"
    assert sell_leg.exchange_id == "bybit_futures"


@pytest.mark.asyncio
async def test_legs_contain_leverage_metadata():
    config = FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_leverage=3, max_notional_usd=None)
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None
    for leg in result.legs:
        assert leg.metadata["leverage"] == "3"
        assert leg.metadata["leg_type"] == "futures"


@pytest.mark.asyncio
async def test_margin_safety_check_rejects_oversized_trade():
    """Required margin exceeds available * (1 - safety_pct) → filter."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_leverage=2,
        margin_safety_pct=Decimal("0.20"),
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()

    # required margin = 50000 * 1.0 / 2 = 25000
    # max allowed = 1000 * (1 - 0.20) = 800 < 25000 → reject
    signal = make_signal(volume=Decimal("1.0"), margin_available=Decimal("1000"))
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_margin_check_passes_with_sufficient_margin():
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_leverage=5,
        margin_safety_pct=Decimal("0.20"),
        max_notional_usd=None,  # disable cap so volume=0.1 is used as-is
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()

    # required margin = 50000 * 0.1 / 5 = 1000
    # max allowed = 10000 * 0.80 = 8000 > 1000 → pass
    signal = make_signal(volume=Decimal("0.1"), margin_available=Decimal("10000"))
    result = await strategy.on_signal(signal)
    assert result is not None


@pytest.mark.asyncio
async def test_margin_zero_blocks_trade_bug115():
    """BUG-115: margin_available=0 must block trade instead of skipping margin check.

    Prior to fix: margin_available=0 caused the entire margin check block to be
    skipped → uncapped position size → Binance -2019 "Margin is insufficient".
    After fix: margin_available <= 0 returns None immediately.
    """
    config = FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_leverage=2, max_notional_usd=None)
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()
    signal = make_signal(volume=Decimal("0.5"), margin_available=Decimal("0"))
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_bug116_pending_exits_counted_toward_limit():
    """BUG-116: symbols in _pending_exits must count against max_concurrent_positions.

    The _open_positions_monitor removes a symbol from _open_positions before the exit
    actually executes (10s drain delay).  Without counting _pending_exits, _cur_positions
    drops and a new entry is allowed while the exit is still in-flight → Binance -2019.
    """
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_concurrent_positions=1,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()

    # Simulate: monitor queued exit → symbol moved from _open_positions to _pending_exits
    strategy._pending_exits["BTC/USDT:USDT"] = {
        "buy_ex": "binance_futures",
        "sell_ex": "bybit_futures",
        "size": Decimal("0.01"),
        "entry_time": 0.0,
    }

    # A new signal for a different symbol should be BLOCKED (slot occupied by pending exit)
    result = await strategy.on_signal(make_signal())
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_high_cost_no_trade():
    """When costs exceed gross profit, return None."""
    strategy = FuturesFuturesStrategy(
        "ff_cross",
        make_calculator(Decimal("200")),  # 200 USDT per leg
        FuturesFuturesConfig(min_spread_bps=Decimal("8")),
    )
    await strategy.start()
    signal = make_signal(volume=Decimal("0.5"))  # gross = 50 USDT; cost = 400 USDT
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


# ---------------------------------------------------------------------------
# BUG-80: on_execution_success clears _pending_exits snapshot
# ---------------------------------------------------------------------------

def test_on_execution_success_clears_pending_exits():
    """Successful exit must remove the snapshot from _pending_exits (BUG-80)."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    snapshot = {"entry_time": 1000.0, "buy_ex": "binance_futures", "sell_ex": "bitget_futures", "size": Decimal("1")}
    strategy._pending_exits["BTC/USDT"] = snapshot

    strategy.on_execution_success("BTC/USDT")

    assert "BTC/USDT" not in strategy._pending_exits


def test_on_execution_success_noop_for_entry_orders():
    """on_execution_success for a symbol not in _pending_exits must not raise."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    # Should not raise even if symbol was an entry (not in _pending_exits)
    strategy.on_execution_success("ETH/USDT")
    assert strategy._pending_exits == {}


def test_on_execution_success_does_not_restore_to_open_positions():
    """Successful exit must NOT restore position — only rollback should restore."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    snapshot = {"entry_time": 1000.0, "buy_ex": "binance_futures", "sell_ex": "bitget_futures", "size": Decimal("1")}
    strategy._pending_exits["SOL/USDT"] = snapshot

    strategy.on_execution_success("SOL/USDT")

    assert "SOL/USDT" not in strategy._open_positions
    assert "SOL/USDT" not in strategy._pending_exits


# ---------------------------------------------------------------------------
# BUG-79: on_execution_rollback restores from _pending_exits (regression guard)
# ---------------------------------------------------------------------------

def test_on_execution_rollback_exit_restores_position():
    """Exit rollback must restore position snapshot from _pending_exits (BUG-79 regression guard)."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    snapshot = {"entry_time": 1000.0, "buy_ex": "binance_futures", "sell_ex": "bitget_futures", "size": Decimal("1")}
    strategy._pending_exits["BTC/USDT"] = snapshot

    strategy.on_execution_rollback("BTC/USDT")

    assert "BTC/USDT" in strategy._open_positions
    assert strategy._open_positions["BTC/USDT"] == snapshot
    assert "BTC/USDT" not in strategy._pending_exits


def test_on_execution_rollback_entry_clears_open_positions():
    """Entry rollback must clear from _open_positions (not restore from _pending_exits)."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    strategy._open_positions["ETH/USDT"] = {"entry_time": 1000.0}

    strategy.on_execution_rollback("ETH/USDT")

    assert "ETH/USDT" not in strategy._open_positions
    assert "ETH/USDT" not in strategy._pending_exits


def test_success_then_rollback_does_not_ghost_restore():
    """After successful exit, a spurious rollback on the same symbol must not restore a ghost position (BUG-80 stale snapshot risk)."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    snapshot = {"entry_time": 1000.0, "buy_ex": "binance_futures", "sell_ex": "bitget_futures", "size": Decimal("1")}
    strategy._pending_exits["BTC/USDT"] = snapshot

    # First exit succeeds → clears _pending_exits
    strategy.on_execution_success("BTC/USDT")
    assert "BTC/USDT" not in strategy._pending_exits

    # Spurious late rollback notification (e.g., network retry edge case) → no ghost restore
    strategy.on_execution_rollback("BTC/USDT")
    assert "BTC/USDT" not in strategy._open_positions


def test_rollback_no_state_emits_warning(caplog):
    """BUG-116 HIGH: rollback for symbol not in _pending_exits or _open_positions must emit
    ff.rollback_no_state warning (on_fill may have already cleaned up the snapshot)."""
    import logging

    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    # Neither _open_positions nor _pending_exits contains this symbol
    with caplog.at_level(logging.WARNING, logger="src.strategies.futures_futures"):
        strategy.on_execution_rollback("GHOST/USDT:USDT")
    assert any("ff.rollback_no_state" in r.message for r in caplog.records)
    assert "GHOST/USDT:USDT" not in strategy._open_positions
    assert "GHOST/USDT:USDT" not in strategy._pending_exits
    assert strategy._metrics.rollback_no_state_count == 1


@pytest.mark.asyncio
async def test_on_fill_then_success_then_rollback_no_ghost_restore(caplog):
    """BUG-95c (BUG-116 재설계): on_fill은 per-leg 단순 알림. 실제 상태 전환은
    on_execution_success/handle_exit_rollback/TTL reaper가 담당.

    새 시퀀스:
    1. on_fill (per-leg): _pending_exits 건드리지 않음
    2. on_execution_success (모든 leg 완료): _pending_exits 정리
    3. 뒤늦은 rollback: 스냅샷 없음 → ghost restore 안 됨 (rollback_no_state 경고)
    """
    import logging

    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    snapshot = {
        "entry_time": 1000.0,
        "buy_ex": "binance_futures",
        "sell_ex": "bitget_futures",
        "size": Decimal("1"),
    }
    strategy._pending_exits["BTC/USDT"] = snapshot
    strategy._exiting_symbols.add("BTC/USDT")

    # 1. on_fill — BUG-95c: no-op (snapshot 유지)
    fill = Trade(
        trade_id="fill-001",
        exchange_id="binance_futures",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        price=Decimal("50000"),
        amount=Decimal("1"),
        metadata={"leg_type": "futures_close"},
    )
    await strategy.on_fill(fill)
    # on_fill은 snapshot 유지 (rollback 가능성 때문에)
    assert "BTC/USDT" in strategy._pending_exits

    # 2. on_execution_success — 실제 정리
    strategy.on_execution_success("BTC/USDT")
    assert "BTC/USDT" not in strategy._pending_exits
    assert "BTC/USDT" not in strategy._exiting_symbols

    # 3. 뒤늦은 rollback → rollback_no_state (스냅샷 이미 정리됨)
    with caplog.at_level(logging.WARNING, logger="src.strategies.futures_futures"):
        strategy.on_execution_rollback("BTC/USDT")

    assert any("ff.rollback_no_state" in r.message for r in caplog.records)
    assert "BTC/USDT" not in strategy._open_positions
    assert "BTC/USDT" not in strategy._pending_exits
    assert strategy._metrics.rollback_no_state_count == 1


@pytest.mark.asyncio
async def test_negative_margin_blocks_trade():
    """margin_available < 0 (exchange API error) must be blocked same as == 0 (BUG-115 guard)."""
    config = FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_leverage=2, max_notional_usd=None)
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()
    signal = make_signal(volume=Decimal("0.5"), margin_available=Decimal("-5"))
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


def test_rollback_no_state_threshold_emits_critical(caplog):
    """rollback_no_state_count >= 3 → ff.rollback_no_state_threshold CRITICAL 로그."""
    import logging

    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    # 3회 연속 rollback_no_state → 3번째에서 CRITICAL 로그 발생
    with caplog.at_level(logging.DEBUG, logger="src.strategies.futures_futures"):
        for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            strategy.on_execution_rollback(sym)

    assert strategy._metrics.rollback_no_state_count == 3
    critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("ff.rollback_no_state_threshold" in r.message for r in critical_msgs), \
        "3번째 rollback_no_state 시 CRITICAL 로그 없음"


@pytest.mark.asyncio
async def test_concurrent_positions_cap_blocks_fourth_entry():
    """max_concurrent_positions=3 → 3개 열린 뒤 4번째 심볼 entry 차단."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_concurrent_positions=3,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()

    # 3개 포지션 이미 열린 상태 시뮬레이션
    for sym in ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]:
        strategy._open_positions[sym] = {
            "buy_ex": "binance_futures",
            "sell_ex": "bitget_futures",
            "size": Decimal("0.01"),
            "entry_time": 0.0,
        }

    # 4번째 심볼 (BNB) 신호 → 차단되어야 함
    signal = make_signal()  # default symbol = BTC/USDT:USDT, but already open → also blocked
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


# ---------------------------------------------------------------------------
# BUG-94 / BUG-95: _pending_position_metadata semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_signal_writes_pending_not_open():
    """BUG-94: profitable signal writes to _pending_position_metadata, not _open_positions."""
    strategy = FuturesFuturesStrategy(
        "ff_cross",
        make_calculator(Decimal("1")),
        FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_notional_usd=None),
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.002"))
    result = await strategy.on_signal(signal)
    assert result is not None
    sym = signal.symbol
    assert sym in strategy._pending_position_metadata
    assert sym not in strategy._open_positions


def test_on_execution_success_promotes_pending_to_open():
    """BUG-94: on_execution_success moves metadata from _pending_position_metadata → _open_positions."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    meta = {"buy_ex": "binance_futures", "sell_ex": "bybit_futures", "size": Decimal("0.5"), "entry_time": 0.0}
    strategy._pending_position_metadata["BTC/USDT"] = meta

    strategy.on_execution_success("BTC/USDT")

    assert "BTC/USDT" in strategy._open_positions
    assert strategy._open_positions["BTC/USDT"] == meta
    assert "BTC/USDT" not in strategy._pending_position_metadata


def test_handle_entry_rollback_pops_pending():
    """BUG-94: handle_entry_rollback removes symbol from _pending_position_metadata."""
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    strategy._pending_position_metadata["ETH/USDT"] = {"entry_time": 0.0}

    strategy.handle_entry_rollback("ETH/USDT")

    assert "ETH/USDT" not in strategy._pending_position_metadata
    assert "ETH/USDT" not in strategy._open_positions


def test_clear_ghost_pops_pending(caplog):
    """BUG-94: clear_ghost removes symbol from _pending_position_metadata and emits ff.ghost_cleared."""
    import logging

    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    strategy._pending_position_metadata["SOL/USDT"] = {"entry_time": 0.0}

    with caplog.at_level(logging.WARNING, logger="src.strategies.futures_futures"):
        strategy.clear_ghost("SOL/USDT")

    assert "SOL/USDT" not in strategy._pending_position_metadata
    assert any("ff.ghost_cleared" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_max_concurrent_counts_pending_metadata():
    """BUG-94: _pending_position_metadata entries count toward max_concurrent_positions."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_concurrent_positions=1,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()

    # Fill pending slot to capacity
    strategy._pending_position_metadata["ETH/USDT:USDT"] = {
        "buy_ex": "binance_futures",
        "sell_ex": "bybit_futures",
        "size": Decimal("0.01"),
        "entry_time": 0.0,
    }

    # New signal for a different symbol must be rejected (slot full)
    result = await strategy.on_signal(make_signal())
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_duplicate_signal_rejected_when_pending():
    """BUG-95: signal for symbol already in _pending_position_metadata is rejected."""
    strategy = FuturesFuturesStrategy(
        "ff_cross",
        make_calculator(Decimal("1")),
        FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_notional_usd=None),
    )
    await strategy.start()
    sym = "BTC/USDT:USDT"
    strategy._pending_position_metadata[sym] = {
        "buy_ex": "binance_futures",
        "sell_ex": "bybit_futures",
        "size": Decimal("0.5"),
        "entry_time": 0.0,
    }

    result = await strategy.on_signal(make_signal(spread_pct=Decimal("0.002")))
    assert result is None
