"""US-089: CEX-DEX spread scanner tests.

TDD RED phase: Tests define the expected interface for:
  1. DEXCostCalculator integration in CexDexStrategy.__init__ / on_signal
  2. scan_spread() method for standalone spread scanning

These tests FAIL until Jennie implements the features.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.cex_dex import AMMSlippageModel, CexDexConfig, CexDexStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_dex_adapter(
    pool_price: Decimal = Decimal("50100"),
    gas: Decimal = Decimal("15"),
    pool_address: str = "0xabc123",
    dex_id: str = "uniswap_v3",
    reserves: tuple[Decimal, Decimal] = (Decimal("100"), Decimal("5000000")),
) -> MagicMock:
    adapter = MagicMock()
    adapter.get_pool_price = AsyncMock(return_value=pool_price)
    adapter.estimate_gas = AsyncMock(return_value=gas)
    adapter.pool_address = pool_address
    adapter.dex_id = dex_id
    adapter.get_pool_reserves = AsyncMock(return_value=reserves)
    return adapter


def make_calculator() -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = Decimal("1")
    return calc


def make_dex_cost_calculator(total_cost_bps: Decimal = Decimal("33")) -> MagicMock:
    mock_dex_cost = MagicMock()
    mock_result = MagicMock()
    mock_result.total_cost_bps = total_cost_bps
    mock_dex_cost.calculate.return_value = mock_result
    return mock_dex_cost


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50000"),
    volume: Decimal = Decimal("0.1"),
    symbol: str = "BTC/USDT",
) -> Signal:
    return Signal(
        strategy_id="cex_dex_test",
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="uniswap_v3",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.001"),
        confidence=0.9,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
    )


def make_strategy(
    config: CexDexConfig | None = None,
    dex_adapter: MagicMock | None = None,
    dex_cost_calculator: MagicMock | None = None,
) -> CexDexStrategy:
    if dex_adapter is None:
        dex_adapter = make_dex_adapter()
    kwargs: dict = {}
    if dex_cost_calculator is not None:
        kwargs["dex_cost_calculator"] = dex_cost_calculator
    return CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=config or CexDexConfig(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# DEXCostCalculator 통합 테스트 (1~5)
# ---------------------------------------------------------------------------


def test_init_with_dex_cost_calculator():
    """dex_cost_calculator 파라미터 저장 확인."""
    mock_dex_cost = make_dex_cost_calculator()
    strategy = make_strategy(dex_cost_calculator=mock_dex_cost)
    assert strategy._dex_cost is mock_dex_cost


def test_init_without_dex_cost_calculator():
    """None 기본값 — dex_cost_calculator 미전달 시 None (하위호환)."""
    strategy = make_strategy()
    assert strategy._dex_cost is None


@pytest.mark.asyncio
async def test_on_signal_uses_dex_cost_calculator():
    """DEXCostCalculator 있으면 total_cost_bps로 비용 계산, TradeRequest 생성."""
    # raw_spread = |50000 - 50150| / 50000 = 30bps
    # total_cost_bps = 5bps → net_edge = 25bps > 10bps min_edge → TradeRequest
    dex_adapter = make_dex_adapter(pool_price=Decimal("50150"), gas=Decimal("1"))
    mock_dex_cost = make_dex_cost_calculator(total_cost_bps=Decimal("5"))
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("10")),
        dex_cost_calculator=mock_dex_cost,
    )
    await strategy.start()

    result = await strategy.on_signal(
        make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50000"), volume=Decimal("0.1"))
    )

    assert result is not None
    mock_dex_cost.calculate.assert_called_once()


@pytest.mark.asyncio
async def test_on_signal_without_dex_cost_falls_back():
    """dex_cost_calculator=None → 기존 friction_cost_pct + gas_pct 경로 사용."""
    # raw_spread = 100bps, friction=10bps, gas≈0.2bps → net_edge≈89.8bps > 5bps
    # Large reserves (10M BTC) → AMM slippage ≈ 0.01bps (negligible)
    dex_adapter = make_dex_adapter(
        pool_price=Decimal("50500"),
        gas=Decimal("1"),
        reserves=(Decimal("10000000"), Decimal("500000000000")),
    )
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("5"), friction_cost_pct=Decimal("0.001")),
    )
    await strategy.start()

    result = await strategy.on_signal(
        make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50000"), volume=Decimal("1.0"))
    )

    # No dex_cost_calculator — falls back to friction path
    assert result is not None


@pytest.mark.asyncio
async def test_on_signal_dex_cost_high_cost_filters():
    """DEXCostCalculator 반환 비용이 스프레드보다 크면 시그널 필터링."""
    # raw_spread = 30bps, total_cost_bps = 50bps → net_edge = -20bps < 10bps → None
    dex_adapter = make_dex_adapter(pool_price=Decimal("50150"), gas=Decimal("1"))
    mock_dex_cost = make_dex_cost_calculator(total_cost_bps=Decimal("50"))
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("10")),
        dex_cost_calculator=mock_dex_cost,
    )
    await strategy.start()

    result = await strategy.on_signal(
        make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50000"), volume=Decimal("0.1"))
    )

    assert result is None
    assert strategy.metrics.signals_filtered >= 1


# ---------------------------------------------------------------------------
# scan_spread 테스트 (6~12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_spread_basic():
    """기본 스프레드 스캔 결과에 필수 필드 포함 여부 확인."""
    strategy = make_strategy()
    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert isinstance(result, dict)
    for field in ("symbol", "cex_mid", "dex_price", "net_spread_bps", "tradeable", "direction"):
        assert field in result, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_scan_spread_tradeable():
    """net_spread > min_edge → tradeable=True."""
    # raw_spread = 100bps, friction=10bps, gas≈0.2bps → net≈89.8bps > 10bps
    dex_adapter = make_dex_adapter(pool_price=Decimal("50500"), gas=Decimal("1"))
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("10"), friction_cost_pct=Decimal("0.001")),
    )

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert result["tradeable"] is True


@pytest.mark.asyncio
async def test_scan_spread_not_tradeable():
    """net_spread <= min_edge → tradeable=False."""
    # raw_spread = 0.2bps, friction=20bps → net≈-19.8bps < 10bps
    dex_adapter = make_dex_adapter(pool_price=Decimal("50001"), gas=Decimal("1"))
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("10"), friction_cost_pct=Decimal("0.002")),
    )

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert result["tradeable"] is False


@pytest.mark.asyncio
async def test_scan_spread_direction_buy_cex():
    """cex_mid < dex_price → direction = buy_cex_sell_dex."""
    dex_adapter = make_dex_adapter(pool_price=Decimal("50500"))
    strategy = make_strategy(dex_adapter=dex_adapter)

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert result["direction"] == "buy_cex_sell_dex"


@pytest.mark.asyncio
async def test_scan_spread_direction_buy_dex():
    """cex_mid > dex_price → direction = buy_dex_sell_cex."""
    dex_adapter = make_dex_adapter(pool_price=Decimal("49500"))
    strategy = make_strategy(dex_adapter=dex_adapter)

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert result["direction"] == "buy_dex_sell_cex"


@pytest.mark.asyncio
async def test_scan_spread_dex_error():
    """DEX 가격 조회 실패 → error 키를 포함한 dict 반환."""
    dex_adapter = MagicMock()
    dex_adapter.get_pool_price = AsyncMock(side_effect=Exception("connection timeout"))
    dex_adapter.estimate_gas = AsyncMock(return_value=Decimal("15"))
    dex_adapter.pool_address = "0xabc"
    dex_adapter.dex_id = "uniswap_v3"
    strategy = make_strategy(dex_adapter=dex_adapter)

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert "error" in result


@pytest.mark.asyncio
async def test_scan_spread_with_dex_cost():
    """DEXCostCalculator 연동 시 비용 반영 후 tradeable 판정 및 calculate() 호출 확인."""
    # raw_spread = 100bps, dex_cost = 5bps → net = 95bps > 10bps → tradeable=True
    dex_adapter = make_dex_adapter(pool_price=Decimal("50500"), gas=Decimal("1"))
    mock_dex_cost = make_dex_cost_calculator(total_cost_bps=Decimal("5"))
    strategy = CexDexStrategy(
        strategy_id="cex_dex_test",
        cost_calculator=make_calculator(),
        dex_adapter=dex_adapter,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=CexDexConfig(min_edge_bps=Decimal("10")),
        dex_cost_calculator=mock_dex_cost,
    )

    result = await strategy.scan_spread("BTC/USDT", cex_mid=Decimal("50000"))

    assert result.get("tradeable") is True
    mock_dex_cost.calculate.assert_called_once()
