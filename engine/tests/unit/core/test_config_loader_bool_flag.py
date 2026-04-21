"""Tests for get_bool_flag() — unified truthy env-var parser (M-3 review fix).

Covers: true/false/1/0/unset/case-insensitive/whitespace variants.
"""
from __future__ import annotations

import pytest

from src.core.config_loader import get_bool_flag


# ---------------------------------------------------------------------------
# Truthy values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON", "y", "Y", "t", "T"])
def test_truthy_values_return_true(val: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOOL_FLAG", val)
    assert get_bool_flag("TEST_BOOL_FLAG") is True


# ---------------------------------------------------------------------------
# Falsy values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val", ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", "n", "N", "f", "F"])
def test_falsy_values_return_false(val: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOOL_FLAG", val)
    assert get_bool_flag("TEST_BOOL_FLAG") is False


# ---------------------------------------------------------------------------
# Unset — default behaviour
# ---------------------------------------------------------------------------


def test_unset_returns_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_BOOL_FLAG", raising=False)
    assert get_bool_flag("TEST_BOOL_FLAG") is False


def test_unset_returns_explicit_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_BOOL_FLAG", raising=False)
    assert get_bool_flag("TEST_BOOL_FLAG", default=True) is True


# ---------------------------------------------------------------------------
# Whitespace stripping
# ---------------------------------------------------------------------------


def test_whitespace_stripped_before_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOOL_FLAG", "  true  ")
    assert get_bool_flag("TEST_BOOL_FLAG") is True


def test_whitespace_falsy_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOOL_FLAG", "  false  ")
    assert get_bool_flag("TEST_BOOL_FLAG") is False


# ---------------------------------------------------------------------------
# Case-sensitive check from old pattern — now handled consistently
# ---------------------------------------------------------------------------


def test_mixed_case_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old == 'true' pattern would miss 'True' or 'TRUE' — get_bool_flag handles all."""
    monkeypatch.setenv("TEST_BOOL_FLAG", "True")
    assert get_bool_flag("TEST_BOOL_FLAG") is True
