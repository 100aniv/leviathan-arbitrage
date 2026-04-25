"""Tests for CrossExchangeStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost_per_leg: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost_per_leg
    return calc


def make_signal(
    spread_pct: Decimal = Decimal("0.002"),  # 20 bps
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
    net_profit: str | None = None,
) -> Signal:
    metadata: dict = {}
    if net_profit is not None:
        metadata["net_profit"] = net_profit
    return Signal(
        strategy_id="cross_exchange_spot_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.95,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_below_spread_threshold_returns_none():
    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(),
        CrossExchangeConfig(min_spread_bps=Decimal("20")),
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.0015"))  # 15 bps < 20 bps
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_signal_above_threshold_generates_trade_request():
    """20 bps spread, net_profit pre-computed by SignalGenerator = 48 USDT."""
    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(Decimal("1")),
        CrossExchangeConfig(min_spread_bps=Decimal("10")),
    )
    await strategy.start()
    # S22: SignalGenerator pre-computes net_profit (gross 50 - friction 2 = 48)
    signal = make_signal(spread_pct=Decimal("0.002"), volume=Decimal("0.5"), net_profit="48")
    result = await strategy.on_signal(signal)

    assert result is not None
    assert result.strategy_id == "cex_spot"
    assert len(result.legs) == 2
    assert result.expected_profit_usdt == Decimal("48")  # pre-computed by SignalGenerator


@pytest.mark.asyncio
async def test_legs_have_correct_exchanges_and_sides():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(), CrossExchangeConfig())
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None

    buy_leg = next(l for l in result.legs if l.side == OrderSide.BUY)
    sell_leg = next(l for l in result.legs if l.side == OrderSide.SELL)
    assert buy_leg.exchange_id == "binance"
    assert sell_leg.exchange_id == "okx"
    assert buy_leg.symbol == "BTC/USDT"
    assert sell_leg.symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_size_capped_by_max_position_size():
    # max_position_size is USD notional: $10010 / avg_price($50050) = 0.2 BTC cap
    config = CrossExchangeConfig(min_spread_bps=Decimal("10"), max_position_size=Decimal("10010"))
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(), config)
    await strategy.start()
    signal = make_signal(volume=Decimal("1.0"))  # more than max
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.legs[0].size == Decimal("0.2")


@pytest.mark.asyncio
async def test_no_trade_when_costs_exceed_profit():
    """SignalGenerator pre-computed net_profit is negative → return None."""
    calc = make_calculator(Decimal("100"))
    strategy = CrossExchangeStrategy("cex_spot", calc, CrossExchangeConfig(min_spread_bps=Decimal("10")))
    await strategy.start()
    # S22: SignalGenerator pre-computes net_profit (gross 50 - cost 200 = -150)
    signal = make_signal(volume=Decimal("0.5"), net_profit="-150")
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator())
    # Not started — is_active == False
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_metrics_track_correctly():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(Decimal("1")))
    await strategy.start()
    signal = make_signal()
    await strategy.on_signal(signal)
    await strategy.on_signal(signal)
    assert strategy.metrics.signals_received == 2
    assert strategy.metrics.trade_requests_generated == 2


@pytest.mark.asyncio
async def test_strategy_uses_precomputed_net_profit():
    """S22: on_signal uses signal.metadata['net_profit'] — no estimate_cost call."""
    calc = make_calculator(Decimal("1"))
    strategy = CrossExchangeStrategy("cex_spot", calc, CrossExchangeConfig(min_spread_bps=Decimal("10")))
    await strategy.start()
    signal = make_signal(net_profit="42")
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.expected_profit_usdt == Decimal("42")


# ---------------------------------------------------------------------------
# BUG-225: per-exchange symbol availability gate
# ---------------------------------------------------------------------------


def make_mock_adapter(exchange_id: str, supported_symbols: set[str] | None = None) -> MagicMock:
    """Return a mock adapter with supports_symbol() wired up."""
    adapter = MagicMock()
    adapter.exchange_id = exchange_id
    if supported_symbols is None:
        adapter.supports_symbol.return_value = True
    else:
        adapter.supports_symbol.side_effect = lambda sym: sym in supported_symbols
    return adapter


def make_signal_for_exchange(
    buy_exchange: str,
    sell_exchange: str,
    symbol: str = "AAVE/USDT",
) -> Signal:
    return Signal(
        strategy_id="cross_exchange_spot_v1",
        symbol=symbol,
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=Decimal("100"),
        sell_price=Decimal("101"),
        spread_pct=Decimal("0.01"),
        confidence=0.9,
        volume=Decimal("10"),
        timestamp=datetime.now(timezone.utc),
        metadata={"net_profit": "5"},
    )


@pytest.mark.asyncio
async def test_bug225_unsupported_symbol_on_sell_exchange_rejected():
    """BUG-225: signal rejected when sell-leg exchange does not list symbol.

    2026-04-22: KRW exchange × USDT pair fix로 인해 BUG-225 unsupported 검증을
    USDT-only 거래소 조합 (binance ↔ bitget)으로 변경.
    """
    bitget = make_mock_adapter("bitget", supported_symbols={"BTC/USDT", "ETH/USDT"})  # AAVE/USDT not listed
    registry = {"binance": make_mock_adapter("binance", supported_symbols={"AAVE/USDT", "BTC/USDT"}), "bitget": bitget}

    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(),
        CrossExchangeConfig(min_spread_bps=Decimal("10")),
        exchange_registry=registry,
    )
    await strategy.start()

    signal = make_signal_for_exchange("binance", "bitget", "AAVE/USDT")
    result = await strategy.on_signal(signal)

    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_bug225_supported_symbol_on_both_legs_passes():
    """BUG-225: signal passes through when both leg exchanges list the symbol.

    2026-04-22: KRW exchange (upbit/bithumb/coinone) × USDT pair는 별도 fix로
    무조건 reject (KRW base 가격 mix 위험). USDT-only 거래소 (binance↔bitget)
    조합으로 변경.
    """
    registry = {
        "binance": make_mock_adapter("binance", supported_symbols={"BTC/USDT", "AAVE/USDT"}),
        "bitget": make_mock_adapter("bitget", supported_symbols={"BTC/USDT", "AAVE/USDT"}),
    }

    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(),
        CrossExchangeConfig(min_spread_bps=Decimal("10")),
        exchange_registry=registry,
    )
    await strategy.start()

    signal = make_signal_for_exchange("binance", "bitget", "AAVE/USDT")
    result = await strategy.on_signal(signal)

    assert result is not None


@pytest.mark.asyncio
async def test_bug225_counter_increments_on_rejection():
    """BUG-225: SIGNALS_REJECTED_SYMBOL_UNSUPPORTED counter increments on rejection.

    2026-04-22: KRW exchange × USDT pair는 BUG-225 supports_symbol 체크 전에
    무조건 reject (별도 KRW data normalization fix). 따라서 BUG-225 unsupported
    체크는 USDT-only 거래소 조합 (binance ↔ bitget)으로 검증.
    """
    from src.infra.metrics import SIGNALS_REJECTED_SYMBOL_UNSUPPORTED

    # bitget이 TAO/USDT 미list → BUG-225 supports_symbol 체크에서 reject
    bitget = make_mock_adapter("bitget", supported_symbols={"BTC/USDT"})
    registry = {"binance": make_mock_adapter("binance", supported_symbols={"TAO/USDT"}), "bitget": bitget}

    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(),
        CrossExchangeConfig(min_spread_bps=Decimal("10")),
        exchange_registry=registry,
    )
    await strategy.start()

    before = SIGNALS_REJECTED_SYMBOL_UNSUPPORTED.labels(
        strategy="cross_exchange_spot", exchange="bitget"
    )._value.get()

    signal = make_signal_for_exchange("binance", "bitget", "TAO/USDT")
    result = await strategy.on_signal(signal)

    after = SIGNALS_REJECTED_SYMBOL_UNSUPPORTED.labels(
        strategy="cross_exchange_spot", exchange="bitget"
    )._value.get()

    assert result is None
    assert after == before + 1
