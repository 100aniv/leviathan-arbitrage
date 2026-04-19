"""Unit tests for /api/v1/positions/hedge-pairs (WS-C2)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app


@pytest.fixture
def context():
    return EngineContext()


@pytest.fixture
def client(context):
    app = create_app(context)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('test')}"}


def _mock_adapter(positions):
    ad = MagicMock()
    ad.get_positions = AsyncMock(return_value=positions)
    return ad


class TestHedgePairsAuth:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/positions/hedge-pairs").status_code == 401

    def test_returns_200_empty_without_engine(self, client, auth_headers):
        data = client.get("/api/v1/positions/hedge-pairs", headers=auth_headers).json()
        assert data == {
            "pairs": [],
            "unpaired_positions": [],
            "total_pairs": 0,
            "total_unrealized": 0.0,
        }


class TestHedgePairsMatching:
    def test_matched_pair_emitted(self, client, context, auth_headers):
        binance_pos = SimpleNamespace(
            symbol="BLUR/USDT",
            size=-339.0,   # short
            entry_price=0.031,
            mark_price=0.0308,
            unrealized_pnl=0.12,
        )
        bitget_pos = SimpleNamespace(
            symbol="BLUR/USDT",
            size=339.0,    # long
            entry_price=0.031,
            mark_price=0.0308,
            unrealized_pnl=-0.13,
        )
        context.engine = SimpleNamespace(_exchanges={
            "binance_futures": _mock_adapter([binance_pos]),
            "bitget_futures": _mock_adapter([bitget_pos]),
        })
        data = client.get("/api/v1/positions/hedge-pairs", headers=auth_headers).json()
        assert data["total_pairs"] == 1
        assert data["pairs"][0]["symbol"] == "BLUR/USDT"
        assert data["pairs"][0]["binance_leg"]["side"] == "SHORT"
        assert data["pairs"][0]["bitget_leg"]["side"] == "LONG"
        assert data["pairs"][0]["net_unrealized"] == pytest.approx(-0.01, abs=0.001)
        assert data["total_unrealized"] == pytest.approx(-0.01, abs=0.001)
        assert data["unpaired_positions"] == []

    def test_unpaired_single_leg(self, client, context, auth_headers):
        orphan = SimpleNamespace(
            symbol="ETH/USDT",
            size=1.0,
            entry_price=3000.0,
            mark_price=3010.0,
            unrealized_pnl=10.0,
        )
        context.engine = SimpleNamespace(_exchanges={
            "binance_futures": _mock_adapter([orphan]),
            "bitget_futures": _mock_adapter([]),
        })
        data = client.get("/api/v1/positions/hedge-pairs", headers=auth_headers).json()
        assert data["total_pairs"] == 0
        assert len(data["unpaired_positions"]) == 1
        assert data["unpaired_positions"][0]["symbol"] == "ETH/USDT"
        assert data["unpaired_positions"][0]["exchange_id"] == "binance_futures"
        assert data["total_unrealized"] == pytest.approx(10.0)

    def test_zero_size_positions_skipped(self, client, context, auth_headers):
        empty = SimpleNamespace(
            symbol="BTC/USDT", size=0.0,
            entry_price=0.0, mark_price=0.0, unrealized_pnl=0.0,
        )
        context.engine = SimpleNamespace(_exchanges={
            "binance_futures": _mock_adapter([empty]),
            "bitget_futures": _mock_adapter([empty]),
        })
        data = client.get("/api/v1/positions/hedge-pairs", headers=auth_headers).json()
        assert data["total_pairs"] == 0
        assert data["unpaired_positions"] == []

    def test_adapter_error_degrades_gracefully(self, client, context, auth_headers):
        broken = MagicMock()
        broken.get_positions = AsyncMock(side_effect=Exception("exchange down"))
        ok_pos = SimpleNamespace(
            symbol="SOL/USDT",
            size=2.0,
            entry_price=100.0,
            mark_price=101.0,
            unrealized_pnl=2.0,
        )
        context.engine = SimpleNamespace(_exchanges={
            "binance_futures": broken,
            "bitget_futures": _mock_adapter([ok_pos]),
        })
        response = client.get("/api/v1/positions/hedge-pairs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Bitget-side orphan should still be in unpaired_positions.
        assert data["total_pairs"] == 0
        assert len(data["unpaired_positions"]) == 1
