"""Unit tests for :class:`ExchangePnLSnapshot` — Path-B Day-1."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reconciliation.exchange_pnl_snapshot import (
    DEFAULT_POLL_INTERVAL_S,
    PNL_CONTRIBUTING_TYPES,
    ExchangePnLSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binance_adapter(rows: list[dict] | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.exchange_id = "binance_futures"
    adapter._market_type = "futures"
    adapter._signed_request = AsyncMock(return_value=rows or [])
    return adapter


def _make_bitget_adapter(
    bills: list[dict] | None = None,
    is_uta: bool = False,
) -> MagicMock:
    adapter = MagicMock()
    adapter.exchange_id = "bitget_futures"
    adapter._market_type = "futures"
    adapter._is_uta = MagicMock(return_value=is_uta)
    adapter._request = AsyncMock(return_value={"data": bills or []})
    return adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_jsonl_path(tmp_path: Path) -> None:
    """No db_pool → _persist writes JSONL."""
    adapter = _make_binance_adapter()
    snap = ExchangePnLSnapshot(
        adapters=[adapter],
        db_pool=None,
        fallback_dir=tmp_path,
    )
    await snap._persist([
        {
            "ts": datetime.now(timezone.utc),
            "ts_ms": 0,
            "exchange": "binance_futures",
            "income_type": "REALIZED_PNL",
            "symbol": "BTCUSDT",
            "asset": "USDT",
            "amount_usd": Decimal("0.50"),
            "tran_id": "tx1",
            "raw_json": {},
        },
    ])
    day_files = list(tmp_path.glob("*.jsonl"))
    assert day_files, "JSONL fallback file not written"
    contents = day_files[0].read_text()
    assert "REALIZED_PNL" in contents
    assert "0.50" in contents


@pytest.mark.asyncio
async def test_get_cumulative_pnl_memory_only(tmp_path: Path) -> None:
    """When DB is absent the memory fallback computes the sum correctly."""
    snap = ExchangePnLSnapshot(adapters=[], db_pool=None, fallback_dir=tmp_path)
    now = datetime.now(timezone.utc)
    snap._fallback_events.extend([
        {
            "ts": now - timedelta(hours=1),
            "exchange": "binance_futures",
            "income_type": "REALIZED_PNL",
            "symbol": "BTCUSDT",
            "asset": "USDT",
            "amount_usd": Decimal("0.75"),
            "tran_id": "a",
            "raw_json": {},
        },
        {
            "ts": now - timedelta(minutes=10),
            "exchange": "binance_futures",
            "income_type": "COMMISSION",
            "symbol": "BTCUSDT",
            "asset": "USDT",
            "amount_usd": Decimal("-0.25"),
            "tran_id": "b",
            "raw_json": {},
        },
        {  # excluded — TRANSFER is capital movement, not PnL
            "ts": now - timedelta(minutes=1),
            "exchange": "binance_futures",
            "income_type": "TRANSFER",
            "symbol": "",
            "asset": "USDT",
            "amount_usd": Decimal("5.00"),
            "tran_id": "c",
            "raw_json": {},
        },
    ])
    total = await snap.get_cumulative_pnl_usd(now - timedelta(days=1))
    assert total == Decimal("0.50")


@pytest.mark.asyncio
async def test_get_daily_pnl_usd_day_boundary(tmp_path: Path) -> None:
    """Events outside the UTC day are excluded from get_daily_pnl_usd."""
    snap = ExchangePnLSnapshot(adapters=[], db_pool=None, fallback_dir=tmp_path)
    today = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    yesterday = today - timedelta(days=1)
    snap._fallback_events.extend([
        {
            "ts": today,
            "exchange": "binance_futures",
            "income_type": "REALIZED_PNL",
            "symbol": "",
            "asset": "USDT",
            "amount_usd": Decimal("1.00"),
            "tran_id": "today",
            "raw_json": {},
        },
        {
            "ts": yesterday,
            "exchange": "binance_futures",
            "income_type": "REALIZED_PNL",
            "symbol": "",
            "asset": "USDT",
            "amount_usd": Decimal("9.99"),
            "tran_id": "yday",
            "raw_json": {},
        },
    ])
    total = await snap.get_daily_pnl_usd(today.date())
    assert total == Decimal("1.00")


@pytest.mark.asyncio
async def test_prime_from_startup_binance(tmp_path: Path) -> None:
    """Prime hits /fapi/v1/income with the lookback window and stores events."""
    rows = [
        {
            "tranId": 1,
            "time": 1700000000000,
            "income": "0.42",
            "incomeType": "REALIZED_PNL",
            "asset": "USDT",
            "symbol": "BTCUSDT",
        },
        {
            "tranId": 2,
            "time": 1700000060000,
            "income": "-0.05",
            "incomeType": "COMMISSION",
            "asset": "USDT",
            "symbol": "BTCUSDT",
        },
    ]
    adapter = _make_binance_adapter(rows=rows)
    snap = ExchangePnLSnapshot(
        adapters=[adapter],
        db_pool=None,
        fallback_dir=tmp_path,
    )
    ingested = await snap.prime_from_startup(lookback_hours=24)
    assert ingested == 2
    assert adapter._signed_request.call_count == 1
    params = adapter._signed_request.call_args.kwargs["params"]
    assert "startTime" in params
    # Two events reflected in memory
    assert len(snap._fallback_events) == 2


@pytest.mark.asyncio
async def test_prime_from_startup_bitget_uta(tmp_path: Path) -> None:
    """Bitget UTA path uses /api/v3/account/financial-records."""
    bills_resp = {
        "data": {
            "list": [
                {
                    "billId": "b1",
                    "cTime": 1700000000000,
                    "businessType": "close_long",
                    "amount": "0.30",
                    "coin": "USDT",
                    "symbol": "BTCUSDT",
                },
            ],
        },
    }
    adapter = _make_bitget_adapter(is_uta=True)
    adapter._request = AsyncMock(return_value=bills_resp)
    snap = ExchangePnLSnapshot(
        adapters=[adapter],
        db_pool=None,
        fallback_dir=tmp_path,
    )
    ingested = await snap.prime_from_startup(lookback_hours=6)
    assert ingested == 1
    path_arg = adapter._request.call_args.args[1]
    assert path_arg == "/api/v3/account/financial-records"
    # Dedup table populated so the steady-state poll skips the same tran_id.
    assert "bitget_futures:b1" in snap._seen_tran_ids["bitget_futures"]


@pytest.mark.asyncio
async def test_prime_failure_is_non_fatal(tmp_path: Path) -> None:
    """An adapter that raises does not abort the prime for others."""
    bad = _make_binance_adapter()
    bad._signed_request = AsyncMock(side_effect=RuntimeError("boom"))
    good = _make_bitget_adapter(is_uta=False)
    good._request = AsyncMock(return_value={"data": []})
    snap = ExchangePnLSnapshot(
        adapters=[bad, good],
        db_pool=None,
        fallback_dir=tmp_path,
    )
    ingested = await snap.prime_from_startup()
    assert ingested == 0  # good returned empty, bad raised — both graceful


@pytest.mark.asyncio
async def test_has_data_reflects_memory_store(tmp_path: Path) -> None:
    snap = ExchangePnLSnapshot(adapters=[], db_pool=None, fallback_dir=tmp_path)
    assert snap.has_data() is False
    snap._fallback_events.append(
        {
            "ts": datetime.now(timezone.utc),
            "exchange": "binance_futures",
            "income_type": "REALIZED_PNL",
            "symbol": "",
            "asset": "USDT",
            "amount_usd": Decimal("0.01"),
            "tran_id": "x",
            "raw_json": {},
        },
    )
    assert snap.has_data() is True


def test_pnl_contributing_types_exclude_transfer() -> None:
    """Sanity: TRANSFER / WELCOME_BONUS must not be counted as PnL."""
    assert "TRANSFER" not in PNL_CONTRIBUTING_TYPES
    assert "WELCOME_BONUS" not in PNL_CONTRIBUTING_TYPES
    assert "REALIZED_PNL" in PNL_CONTRIBUTING_TYPES
    assert "COMMISSION" in PNL_CONTRIBUTING_TYPES
    assert "FUNDING_FEE" in PNL_CONTRIBUTING_TYPES


def test_default_poll_interval_is_60s() -> None:
    assert DEFAULT_POLL_INTERVAL_S == 60.0
