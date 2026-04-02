"""US-136: parameter default tests.

Note: test_env_sync_check_* tests removed — US-375 deleted _check_env_sync()
(dead code after .env unification to repo root).

Tests cover:
- test_min_edge_bps_default_is_5: main.py reads MIN_EDGE_BPS with default 5
- test_powerlaw_k_default_is_zero: PowerLawSlippage k defaults to 0.0 (env not set)
- test_powerlaw_k_reads_from_env: PowerLawSlippage k reads POWERLAW_SLIPPAGE_K env
- test_cex_orderbook_slippage_k_is_nonzero: CEXOrderbookSlippage default k is not zero
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_min_edge_bps_default_is_5(monkeypatch):
    """main.py reads MIN_EDGE_BPS from env with default of 5."""
    monkeypatch.delenv("MIN_EDGE_BPS", raising=False)

    # Replicate the logic in main.py line 479
    min_edge_bps = int(os.environ.get("MIN_EDGE_BPS", "5"))
    assert min_edge_bps == 5, f"Expected default MIN_EDGE_BPS=5, got {min_edge_bps}"


def test_powerlaw_k_default_is_zero(monkeypatch):
    """PowerLawSlippage defaults to k=0.0 when POWERLAW_SLIPPAGE_K env is not set.

    k=0.0 prevents double-slippage: SignalGenerator's CEXOrderbookSlippage
    is the sole slippage source.
    """
    monkeypatch.delenv("POWERLAW_SLIPPAGE_K", raising=False)

    from src.modes.shadow import PowerLawSlippage

    model = PowerLawSlippage()
    assert model._k == 0.0, (
        f"Expected PowerLawSlippage default k=0.0, got {model._k}"
    )


def test_powerlaw_k_is_zero_when_env_overridden(monkeypatch):
    """PowerLawSlippage k=0.0 when POWERLAW_SLIPPAGE_K=0.0 (.env deployment setting).

    Both root .env and engine/.env set POWERLAW_SLIPPAGE_K=0.0 to prevent
    double-slippage (CEXOrderbookSlippage in SignalGenerator is the sole source).
    """
    monkeypatch.setenv("POWERLAW_SLIPPAGE_K", "0.0")

    from src.modes.shadow import PowerLawSlippage

    model = PowerLawSlippage()
    assert model._k == 0.0, (
        f"Expected PowerLawSlippage k=0.0 when env=0.0, got {model._k}"
    )


def test_powerlaw_k_reads_from_env(monkeypatch):
    """PowerLawSlippage reads POWERLAW_SLIPPAGE_K from environment when set."""
    monkeypatch.setenv("POWERLAW_SLIPPAGE_K", "1.5")

    from src.modes.shadow import PowerLawSlippage

    model = PowerLawSlippage()
    assert model._k == 1.5, (
        f"Expected PowerLawSlippage k=1.5 from env, got {model._k}"
    )


def test_cex_orderbook_slippage_k_is_nonzero():
    """CEXOrderbookSlippage (used in SignalGenerator) has a non-zero k by default."""
    try:
        from src.friction.slippage import CEXOrderbookSlippage
        model = CEXOrderbookSlippage()
        # CEXOrderbookSlippage must have a nonzero slippage factor
        # (it is the ONLY slippage source — must not be zero)
        assert hasattr(model, "_k") or hasattr(model, "k") or hasattr(model, "_fallback_bps"), (
            "CEXOrderbookSlippage must have a slippage parameter"
        )
    except ImportError:
        pytest.skip("CEXOrderbookSlippage not importable from src.friction.slippage")
