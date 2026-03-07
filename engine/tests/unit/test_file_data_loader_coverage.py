"""Coverage tests for file_data_loader.py — targeting 90%+ coverage."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.tuning.data_loader import OHLCVWindow
from src.tuning.file_data_loader import (
    FileDataLoader,
    generate_synthetic_ohlcv,
    generate_synthetic_spreads,
    load_csv_ohlcv,
)


# ---------------------------------------------------------------------------
# generate_synthetic_ohlcv
# ---------------------------------------------------------------------------


class TestGenerateSyntheticOHLCV:
    def test_default_returns_ohlcv_window(self):
        result = generate_synthetic_ohlcv()
        assert isinstance(result, OHLCVWindow)
        assert result.length == 2000

    def test_custom_candle_count(self):
        result = generate_synthetic_ohlcv(num_candles=100)
        assert result.length == 100

    def test_with_seed_none_still_returns_data(self):
        # Lines 46-47: seed=None path (rng = random.Random(), np_rng = np.random.RandomState())
        result = generate_synthetic_ohlcv(num_candles=50, seed=None)
        assert isinstance(result, OHLCVWindow)
        assert result.length == 50

    def test_no_spread_injection(self):
        result = generate_synthetic_ohlcv(spread_injection_rate=0.0, num_candles=100)
        assert result.length == 100

    def test_reproducible_with_same_seed(self):
        r1 = generate_synthetic_ohlcv(seed=42, num_candles=100)
        r2 = generate_synthetic_ohlcv(seed=42, num_candles=100)
        np.testing.assert_array_equal(r1.closes, r2.closes)

    def test_prices_are_positive(self):
        result = generate_synthetic_ohlcv(seed=1, num_candles=200)
        assert np.all(result.closes > 0)
        assert np.all(result.highs >= result.closes)
        assert np.all(result.lows <= result.closes)

    def test_all_arrays_same_length(self):
        result = generate_synthetic_ohlcv(seed=7, num_candles=50)
        assert len(result.times) == 50
        assert len(result.opens) == 50
        assert len(result.highs) == 50
        assert len(result.lows) == 50
        assert len(result.closes) == 50
        assert len(result.volumes) == 50


# ---------------------------------------------------------------------------
# generate_synthetic_spreads
# ---------------------------------------------------------------------------


class TestGenerateSyntheticSpreads:
    def test_default_returns_2000_records(self):
        records = generate_synthetic_spreads()
        assert len(records) == 2000

    def test_with_seed_none(self):
        # Line 110: seed=None path (rng = random.Random())
        records = generate_synthetic_spreads(num_records=30, seed=None)
        assert len(records) == 30

    def test_custom_count(self):
        records = generate_synthetic_spreads(num_records=50, seed=10)
        assert len(records) == 50

    def test_record_has_expected_fields(self):
        records = generate_synthetic_spreads(num_records=10, seed=5)
        r = records[0]
        assert r.strategy == "cross_exchange"
        assert r.exchange_pair == "binance-upbit"
        assert hasattr(r, "gross_spread")
        assert hasattr(r, "net_spread")

    def test_opportunity_injection_changes_spread(self):
        # Records with high opportunity_rate should have higher gross spreads on average
        records_high = generate_synthetic_spreads(num_records=500, opportunity_rate=1.0, seed=99)
        records_low = generate_synthetic_spreads(num_records=500, opportunity_rate=0.0, seed=99)
        avg_high = sum(r.gross_spread for r in records_high) / len(records_high)
        avg_low = sum(r.gross_spread for r in records_low) / len(records_low)
        assert avg_high > avg_low


# ---------------------------------------------------------------------------
# load_csv_ohlcv
# ---------------------------------------------------------------------------


def _write_csv_file(rows: list[dict], path: Path) -> Path:
    if not rows:
        path.write_text("")
        return path
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestLoadCsvOHLCV:
    def test_loads_iso_time_format(self, tmp_path):
        # Lines 154-173: ISO 8601 time parsing
        path = _write_csv_file(
            [
                {"time": "2024-01-01T00:00:00", "open": "50000", "high": "51000", "low": "49000", "close": "50500", "volume": "10"},
                {"time": "2024-01-01T00:01:00", "open": "50500", "high": "51500", "low": "50000", "close": "51000", "volume": "15"},
            ],
            tmp_path / "data.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 2
        assert result.closes[0] == pytest.approx(50500.0)
        assert result.closes[1] == pytest.approx(51000.0)

    def test_loads_with_timestamp_column_name(self, tmp_path):
        path = _write_csv_file(
            [{"timestamp": "2024-01-01T00:00:00Z", "open": "100", "high": "110", "low": "90", "close": "105", "volume": "5"}],
            tmp_path / "ts.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 1

    def test_loads_unix_timestamp_ms(self, tmp_path):
        # Line 163: unix timestamp fallback
        ts_ms = int(datetime(2024, 1, 1).timestamp() * 1000)
        path = _write_csv_file(
            [{"time": str(ts_ms), "open": "100", "high": "110", "low": "90", "close": "105", "volume": "5"}],
            tmp_path / "unix.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 1

    def test_skips_row_with_unparseable_time(self, tmp_path):
        # Lines 164-165: continue on unparseable time
        path = _write_csv_file(
            [
                {"time": "not_a_time_value", "open": "100", "high": "110", "low": "90", "close": "105", "volume": "5"},
                {"time": "2024-01-01T00:00:00", "open": "200", "high": "210", "low": "190", "close": "205", "volume": "5"},
            ],
            tmp_path / "skip.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 1
        assert result.closes[0] == pytest.approx(205.0)

    def test_skips_row_with_empty_time(self, tmp_path):
        path = _write_csv_file(
            [
                {"time": "", "open": "100", "high": "110", "low": "90", "close": "105", "volume": "5"},
                {"time": "2024-06-01T00:00:00", "open": "200", "high": "210", "low": "190", "close": "205", "volume": "5"},
            ],
            tmp_path / "empty_time.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 1

    def test_returns_correct_array_types(self, tmp_path):
        path = _write_csv_file(
            [{"time": "2024-01-01T00:00:00", "open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5", "volume": "100"}],
            tmp_path / "types.csv",
        )
        result = load_csv_ohlcv(path)
        assert isinstance(result, OHLCVWindow)
        assert result.opens.dtype == float
        assert result.volumes.dtype == float

    def test_handles_utc_z_suffix(self, tmp_path):
        path = _write_csv_file(
            [{"time": "2024-01-01T00:00:00Z", "open": "50000", "high": "51000", "low": "49000", "close": "50500", "volume": "10"}],
            tmp_path / "utcz.csv",
        )
        result = load_csv_ohlcv(path)
        assert result.length == 1


# ---------------------------------------------------------------------------
# FileDataLoader
# ---------------------------------------------------------------------------


class TestFileDataLoader:
    def test_load_synthetic_returns_ohlcv(self):
        # Lines 213-214
        loader = FileDataLoader()
        result = loader.load("synthetic")
        assert isinstance(result, OHLCVWindow)
        assert result.length == 2000

    def test_load_synthetic_is_cached(self):
        # Lines 210-211: cache hit returns same object
        loader = FileDataLoader()
        r1 = loader.load("synthetic")
        r2 = loader.load("synthetic")
        assert r1 is r2

    def test_load_csv_file(self, tmp_path):
        # Lines 215-216: CSV path
        path = _write_csv_file(
            [{"time": "2024-01-01T00:00:00", "open": "50000", "high": "51000", "low": "49000", "close": "50500", "volume": "10"}],
            tmp_path / "data.csv",
        )
        loader = FileDataLoader()
        result = loader.load(str(path))
        assert result.length == 1

    def test_load_csv_is_cached(self, tmp_path):
        path = _write_csv_file(
            [{"time": "2024-01-01T00:00:00", "open": "50000", "high": "51000", "low": "49000", "close": "50500", "volume": "10"}],
            tmp_path / "cache.csv",
        )
        loader = FileDataLoader()
        r1 = loader.load(str(path))
        r2 = loader.load(str(path))
        assert r1 is r2

    def test_slice_window_returns_correct_subset(self):
        loader = FileDataLoader()
        window = generate_synthetic_ohlcv(num_candles=100, seed=42)
        sliced = loader.slice_window(window, 10, 50)
        assert sliced.length == 40
        np.testing.assert_array_equal(sliced.closes, window.closes[10:50])
        np.testing.assert_array_equal(sliced.opens, window.opens[10:50])
        np.testing.assert_array_equal(sliced.volumes, window.volumes[10:50])

    def test_slice_window_full_range(self):
        loader = FileDataLoader()
        window = generate_synthetic_ohlcv(num_candles=50, seed=1)
        sliced = loader.slice_window(window, 0, 50)
        assert sliced.length == 50
