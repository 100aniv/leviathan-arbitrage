"""Unit tests for ExchangeIncomeFetcher (WS-A2)."""
from __future__ import annotations

import asyncio
import csv
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.exchange.exchange_income_fetcher import (
    _BITGET_BUSINESS_TYPE_MAP,
    ExchangeIncomeFetcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binance_adapter(income_payload: list[dict]) -> MagicMock:
    """Mock Binance futures adapter with a _signed_request that returns the given payload."""
    adapter = MagicMock()
    adapter.exchange_id = "binance_futures"
    adapter._market_type = "futures"
    adapter._signed_request = AsyncMock(return_value=income_payload)
    return adapter


def _make_bitget_adapter(bill_payload: dict, is_uta: bool = False) -> MagicMock:
    """Mock Bitget futures adapter with a _request that returns the given payload."""
    adapter = MagicMock()
    adapter.exchange_id = "bitget_futures"
    adapter._market_type = "futures"
    adapter._is_uta = MagicMock(return_value=is_uta)
    adapter._request = AsyncMock(return_value=bill_payload)
    return adapter


# ---------------------------------------------------------------------------
# Binance classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binance_classification(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    payload = [
        {
            "symbol": "BTCUSDT",
            "incomeType": "REALIZED_PNL",
            "income": "1.25",
            "asset": "USDT",
            "time": now_ms,
            "tranId": "tx1",
        },
        {
            "symbol": "BTCUSDT",
            "incomeType": "COMMISSION",
            "income": "-0.04",
            "asset": "USDT",
            "time": now_ms,
            "tranId": "tx2",
        },
        {
            "symbol": "ETHUSDT",
            "incomeType": "FUNDING_FEE",
            "income": "-0.12",
            "asset": "USDT",
            "time": now_ms,
            "tranId": "tx3",
        },
    ]

    adapter = _make_binance_adapter(payload)
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )

    events = await fetcher._fetch_binance(adapter)

    assert len(events) == 3
    types = {e["income_type"] for e in events}
    assert types == {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
    # Check amount preservation
    by_tran = {e["tran_id"]: e["amount_usdt"] for e in events}
    assert by_tran["tx1"] == pytest.approx(1.25)
    assert by_tran["tx2"] == pytest.approx(-0.04)
    assert by_tran["tx3"] == pytest.approx(-0.12)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_on_repeated_tran_id(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    payload = [
        {"incomeType": "REALIZED_PNL", "income": "1.0", "asset": "USDT",
         "symbol": "BTCUSDT", "time": now_ms, "tranId": "dup1"},
    ]
    adapter = _make_binance_adapter(payload)
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )

    first = await fetcher._fetch_binance(adapter)
    second = await fetcher._fetch_binance(adapter)

    assert len(first) == 1
    assert len(second) == 0  # dedup by tran_id


# ---------------------------------------------------------------------------
# CSV format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_append_format(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    payload = [
        {"incomeType": "FUNDING_FEE", "income": "-0.50", "asset": "USDT",
         "symbol": "BTCUSDT", "time": now_ms, "tranId": "csv1"},
    ]
    adapter = _make_binance_adapter(payload)
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )

    events = await fetcher._fetch_binance(adapter)
    fetcher._emit("binance_futures", events)
    fetcher._append_csv("binance_futures", events)

    csv_files = list(tmp_path.glob("exchange_income_*.csv"))
    assert len(csv_files) == 1
    with csv_files[0].open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["exchange"] == "binance_futures"
    assert rows[0]["income_type"] == "FUNDING_FEE"
    assert rows[0]["asset"] == "USDT"
    assert rows[0]["symbol"] == "BTCUSDT"
    assert float(rows[0]["amount_usdt"]) == pytest.approx(-0.50)
    assert rows[0]["tran_id"] == "csv1"
    expected_cols = {"timestamp", "exchange", "income_type", "asset",
                     "symbol", "amount_usdt", "tran_id"}
    assert set(rows[0].keys()) == expected_cols


# ---------------------------------------------------------------------------
# Bitget classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bitget_business_type_map(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    payload = {
        "code": "00000",
        "data": {
            "bills": [
                {
                    "billId": "b1", "symbol": "BTCUSDT",
                    "amount": "0.8", "fee": "-0.03",
                    "businessType": "close_long", "coin": "USDT",
                    "cTime": str(now_ms),
                },
                {
                    "billId": "b2", "symbol": "BTCUSDT",
                    "amount": "-0.005", "fee": "0",
                    "businessType": "contract_settle_fee", "coin": "USDT",
                    "cTime": str(now_ms),
                },
            ],
        },
    }
    adapter = _make_bitget_adapter(payload, is_uta=False)
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )

    events = await fetcher._fetch_bitget(adapter)

    # close_long → REALIZED_PNL + COMMISSION (fee), contract_settle_fee → FUNDING_FEE
    types = [e["income_type"] for e in events]
    assert "REALIZED_PNL" in types
    assert "COMMISSION" in types
    assert "FUNDING_FEE" in types
    assert _BITGET_BUSINESS_TYPE_MAP["contract_settle_fee"] == "FUNDING_FEE"


# ---------------------------------------------------------------------------
# Start/stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_skips_spot_adapters(tmp_path: Path) -> None:
    spot = MagicMock()
    spot.exchange_id = "binance"
    spot._market_type = "spot"

    fetcher = ExchangeIncomeFetcher(
        adapters=[spot],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )
    await fetcher.start()
    assert fetcher._tasks == []
    await fetcher.stop()


@pytest.mark.asyncio
async def test_start_stop_futures_adapter(tmp_path: Path) -> None:
    adapter = _make_binance_adapter([])
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
    )
    await fetcher.start()
    assert len(fetcher._tasks) == 1
    # Let the loop run once
    await asyncio.sleep(0.05)
    await fetcher.stop()
    assert all(t.done() for t in fetcher._tasks) or fetcher._tasks == []


# ---------------------------------------------------------------------------
# Reconciliation variance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_variance(tmp_path: Path) -> None:
    adapter = _make_binance_adapter([])
    fetcher = ExchangeIncomeFetcher(
        adapters=[adapter],
        poll_interval_s=0.01,
        csv_dir=str(tmp_path),
        engine_pnl_getter=lambda: 1.0,  # engine claims +$1
    )
    # Simulate exchange reporting -$1 over 24h
    now_ms = int(time.time() * 1000)
    fetcher._income_24h["binance_futures"] = [(now_ms, -1.0)]

    # Does not raise
    fetcher._update_reconciliation("binance_futures")
