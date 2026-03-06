"""CSV and synthetic data loader — no TimescaleDB required.

Generates GBM-based synthetic OHLCV data or loads from CSV files.
Implements the same slice_window() interface as DataLoader for optimizer compatibility.
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.tuning.data_loader import OHLCVWindow, SpreadRecord


def generate_synthetic_ohlcv(
    num_candles: int = 2000,
    base_price: float = 50_000.0,
    volatility: float = 0.02,
    drift: float = 0.0001,
    interval_minutes: int = 1,
    spread_injection_rate: float = 0.15,
    spread_injection_bps: float = 30.0,
    seed: int | None = 42,
) -> OHLCVWindow:
    """Generate synthetic OHLCV data using geometric Brownian motion.

    Args:
        num_candles: Number of candles to generate.
        base_price: Starting price.
        volatility: Per-candle volatility (σ).
        drift: Per-candle drift (μ).
        interval_minutes: Minutes between candles.
        spread_injection_rate: Fraction of candles with injected spread.
        spread_injection_bps: Basis points of injected spread.
        seed: Random seed for reproducibility.
    """
    if seed is not None:
        rng = random.Random(seed)
        np_rng = np.random.RandomState(seed)
    else:
        rng = random.Random()
        np_rng = np.random.RandomState()

    dt = 1.0  # unit time step per candle
    prices = np.empty(num_candles, dtype=float)
    prices[0] = base_price

    # GBM: S(t+1) = S(t) * exp((μ - σ²/2)*dt + σ*√dt*Z)
    z = np_rng.standard_normal(num_candles - 1)
    log_returns = (drift - 0.5 * volatility**2) * dt + volatility * math.sqrt(dt) * z
    prices[1:] = base_price * np.exp(np.cumsum(log_returns))

    # Inject spread opportunities
    if spread_injection_rate > 0:
        for i in range(1, num_candles):
            if rng.random() < spread_injection_rate:
                direction = rng.choice([1, -1])
                offset = prices[i] * spread_injection_bps * 0.0001 * direction
                prices[i] += offset

    # Build OHLCV from close prices
    start_time = datetime(2024, 1, 1)
    times = np.array(
        [start_time + timedelta(minutes=i * interval_minutes) for i in range(num_candles)],
        dtype="datetime64[ms]",
    )

    # Simulate OHLCV from close prices with realistic wicks
    opens = np.empty(num_candles, dtype=float)
    highs = np.empty(num_candles, dtype=float)
    lows = np.empty(num_candles, dtype=float)
    closes = prices.copy()
    volumes = np_rng.uniform(10, 100, num_candles)

    opens[0] = base_price
    for i in range(1, num_candles):
        opens[i] = closes[i - 1]

    wick_factor = np_rng.uniform(0.0005, 0.003, num_candles)
    highs = np.maximum(opens, closes) * (1 + wick_factor)
    lows = np.minimum(opens, closes) * (1 - wick_factor)

    return OHLCVWindow(
        times=times,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )


def generate_synthetic_spreads(
    num_records: int = 2000,
    base_spread_bps: float = 5.0,
    spread_volatility: float = 3.0,
    opportunity_rate: float = 0.1,
    opportunity_bps: float = 20.0,
    seed: int | None = 42,
) -> list[SpreadRecord]:
    """Generate synthetic spread records for spread-based backtesting."""
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    records: list[SpreadRecord] = []
    start_time = datetime(2024, 1, 1)

    for i in range(num_records):
        base = base_spread_bps * 0.0001
        noise = rng.gauss(0, spread_volatility * 0.0001)
        gross = base + noise

        # Inject profitable opportunities
        if rng.random() < opportunity_rate:
            gross += opportunity_bps * 0.0001

        fee = 0.001  # 10 bps round-trip
        net = gross - fee

        records.append(
            SpreadRecord(
                time=start_time + timedelta(minutes=i),
                strategy="cross_exchange",
                exchange_pair="binance-upbit",
                gross_spread=gross,
                net_spread=net,
            )
        )

    return records


def load_csv_ohlcv(path: str | Path) -> OHLCVWindow:
    """Load OHLCV data from a CSV file.

    Expected columns: time, open, high, low, close, volume
    The time column should be ISO 8601 format or Unix timestamp (ms).
    """
    path = Path(path)
    times_list: list[datetime] = []
    opens_list: list[float] = []
    highs_list: list[float] = []
    lows_list: list[float] = []
    closes_list: list[float] = []
    volumes_list: list[float] = []

    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse time
            time_str = row.get("time") or row.get("timestamp") or row.get("date", "")
            try:
                t = datetime.fromisoformat(time_str.replace("Z", "+00:00").replace("+00:00", ""))
            except (ValueError, AttributeError):
                try:
                    t = datetime.fromtimestamp(int(time_str) / 1000)
                except (ValueError, TypeError):
                    continue

            times_list.append(t)
            opens_list.append(float(row.get("open", 0)))
            highs_list.append(float(row.get("high", 0)))
            lows_list.append(float(row.get("low", 0)))
            closes_list.append(float(row.get("close", 0)))
            volumes_list.append(float(row.get("volume", 0)))

    return OHLCVWindow(
        times=np.array(times_list, dtype="datetime64[ms]"),
        opens=np.array(opens_list, dtype=float),
        highs=np.array(highs_list, dtype=float),
        lows=np.array(lows_list, dtype=float),
        closes=np.array(closes_list, dtype=float),
        volumes=np.array(volumes_list, dtype=float),
    )


class FileDataLoader:
    """Drop-in replacement for DataLoader that works without TimescaleDB.

    Provides the same slice_window() interface used by WalkForwardOptimizer.
    """

    def __init__(self) -> None:
        self._cache: dict[str, OHLCVWindow] = {}

    def slice_window(self, window: OHLCVWindow, start_idx: int, end_idx: int) -> OHLCVWindow:
        """Return a sub-slice of an OHLCVWindow."""
        return OHLCVWindow(
            times=window.times[start_idx:end_idx],
            opens=window.opens[start_idx:end_idx],
            highs=window.highs[start_idx:end_idx],
            lows=window.lows[start_idx:end_idx],
            closes=window.closes[start_idx:end_idx],
            volumes=window.volumes[start_idx:end_idx],
        )

    def load(self, source: str) -> OHLCVWindow:
        """Load data from file path or generate synthetic data.

        Args:
            source: "synthetic" for GBM data, or a file path for CSV.
        """
        if source in self._cache:
            return self._cache[source]

        if source == "synthetic":
            window = generate_synthetic_ohlcv()
        else:
            window = load_csv_ohlcv(source)

        self._cache[source] = window
        return window
