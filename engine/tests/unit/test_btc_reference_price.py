"""Tests for dynamic BTC reference price configuration."""
import importlib
import os
from decimal import Decimal

import pytest


def test_default_btc_reference_price():
    """Default BTC reference price is 50000 when env var not set."""
    # Ensure env var is not set
    env_val = os.environ.pop("BTC_REFERENCE_PRICE", None)
    try:
        import src.main as main_mod
        importlib.reload(main_mod)
        assert main_mod._BTC_REFERENCE_PRICE == Decimal("50000")
    finally:
        if env_val is not None:
            os.environ["BTC_REFERENCE_PRICE"] = env_val
        importlib.reload(main_mod)


def test_custom_btc_reference_price():
    """BTC reference price reads from BTC_REFERENCE_PRICE env var."""
    old = os.environ.get("BTC_REFERENCE_PRICE")
    os.environ["BTC_REFERENCE_PRICE"] = "85000"
    try:
        import src.main as main_mod
        importlib.reload(main_mod)
        assert main_mod._BTC_REFERENCE_PRICE == Decimal("85000")
    finally:
        if old is not None:
            os.environ["BTC_REFERENCE_PRICE"] = old
        else:
            os.environ.pop("BTC_REFERENCE_PRICE", None)
        importlib.reload(main_mod)


def test_btc_reference_price_is_decimal():
    """BTC reference price is a Decimal, not float."""
    from src.main import _BTC_REFERENCE_PRICE
    assert isinstance(_BTC_REFERENCE_PRICE, Decimal)
