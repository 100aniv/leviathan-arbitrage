"""Tests for telegram_charts (US-291-i)."""
import pytest
from src.infra.telegram_charts import generate_chart, _MPL_AVAILABLE


class TestTelegramCharts:
    @pytest.mark.asyncio
    async def test_no_data(self):
        result = await generate_chart("pnl", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_chart_type(self):
        result = await generate_chart("unknown", {"pnl_history": [1, 2, 3]})
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_pnl_history(self):
        result = await generate_chart("pnl", {"pnl_history": []})
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _MPL_AVAILABLE, reason="matplotlib not installed")
    async def test_pnl_chart_generates_png(self):
        data = {"pnl_history": [0.0, 1.0, 0.5, 2.0, 1.5]}
        result = await generate_chart("pnl", data)
        assert result is not None
        assert isinstance(result, bytes)
        assert result[:4] == b'\x89PNG'  # PNG magic bytes

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _MPL_AVAILABLE, reason="matplotlib not installed")
    async def test_strategy_chart(self):
        data = {
            "by_strategy": [
                {"strategy_id": "arb", "pnl": 1.0},
                {"strategy_id": "tri", "pnl": -0.5},
            ]
        }
        result = await generate_chart("strategy", data)
        assert result is not None
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _MPL_AVAILABLE, reason="matplotlib not installed")
    async def test_mdd_chart(self):
        data = {"mdd_history": [0.01, 0.02, 0.015, 0.03]}
        result = await generate_chart("mdd", data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_strategy_chart_empty(self):
        result = await generate_chart("strategy", {"by_strategy": []})
        assert result is None

    @pytest.mark.asyncio
    async def test_mdd_chart_empty(self):
        result = await generate_chart("mdd", {"mdd_history": []})
        assert result is None
