"""WS-D5 unit tests — daily TCA CSV writer."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.analysis.tca_csv_writer import TCACsvWriter, _CSV_COLUMNS


@pytest.fixture
def tmp_writer(tmp_path: Path) -> TCACsvWriter:
    return TCACsvWriter(base_dir=tmp_path)


class TestTCACsvWriter:
    def test_creates_file_and_header_on_first_write(
        self, tmp_path: Path, tmp_writer: TCACsvWriter
    ) -> None:
        now = datetime(2026, 4, 19, 12, 30, tzinfo=timezone.utc)
        ok = tmp_writer.write_row(
            {
                "strategy": "ff", "symbol": "BTC/USDT", "exchange_pair": "binance:bitget",
                "gross_bps": "40.0", "commission_bps": "10.0", "slippage_bps": "18.0",
                "funding_bps": "5.0", "realized_pnl_usd": "1.2", "exchange_pnl_usd": "1.2",
                "net_pnl_usd": "1.2", "divergence_pct": "0.5", "pnl_source": "exchange_realized_pnl",
            },
            now=now,
        )
        assert ok is True
        csv_path = tmp_path / "20260419.csv"
        assert csv_path.exists()
        with csv_path.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        for col in _CSV_COLUMNS:
            assert col in rows[0]
        assert rows[0]["strategy"] == "ff"
        assert rows[0]["exchange_pair"] == "binance:bitget"
        assert rows[0]["timestamp"] == now.isoformat()

    def test_appends_subsequent_rows_without_duplicate_header(
        self, tmp_path: Path, tmp_writer: TCACsvWriter
    ) -> None:
        now = datetime(2026, 4, 19, 9, 0, tzinfo=timezone.utc)
        for i in range(3):
            tmp_writer.write_row(
                {"strategy": f"s{i}", "pnl_source": "fill_minus_fee"}, now=now,
            )
        csv_path = tmp_path / "20260419.csv"
        with csv_path.open() as fh:
            lines = fh.readlines()
        # 1 header + 3 rows = 4 lines
        assert len(lines) == 4

    def test_rolls_to_new_file_on_new_utc_day(
        self, tmp_path: Path, tmp_writer: TCACsvWriter
    ) -> None:
        day1 = datetime(2026, 4, 19, tzinfo=timezone.utc)
        day2 = datetime(2026, 4, 20, tzinfo=timezone.utc)
        tmp_writer.write_row({"strategy": "x"}, now=day1)
        tmp_writer.write_row({"strategy": "y"}, now=day2)
        assert (tmp_path / "20260419.csv").exists()
        assert (tmp_path / "20260420.csv").exists()

    def test_missing_keys_filled_with_empty_strings(
        self, tmp_path: Path, tmp_writer: TCACsvWriter
    ) -> None:
        now = datetime(2026, 4, 19, tzinfo=timezone.utc)
        tmp_writer.write_row({"strategy": "ff"}, now=now)
        with (tmp_path / "20260419.csv").open() as fh:
            row = next(csv.DictReader(fh))
        # Every column present, unknown ones empty
        assert row["strategy"] == "ff"
        assert row["symbol"] == ""
        assert row["net_pnl_usd"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short", "--no-cov"])
