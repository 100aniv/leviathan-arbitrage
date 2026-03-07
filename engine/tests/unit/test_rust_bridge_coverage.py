"""Coverage tests for src/core/rust_bridge.py — feature flags, fallback, import errors."""
from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock, patch


def _reset_bridge():
    """Reset module-level lazy state for test isolation."""
    import src.core.rust_bridge as rb
    rb._rust_module = False  # False = not yet attempted
    rb._flags = None         # None = not yet parsed


# ---------------------------------------------------------------------------
# _parse_feature_flag
# ---------------------------------------------------------------------------

class TestParseFeatureFlag:
    def setup_method(self):
        _reset_bridge()

    def test_truthy_values(self):
        from src.core.rust_bridge import _parse_feature_flag
        for val in ("true", "1", "yes", "TRUE", "YES", "True"):
            with patch.dict("os.environ", {"_TEST_FLAG": val}):
                assert _parse_feature_flag("_TEST_FLAG") is True

    def test_falsy_values(self):
        from src.core.rust_bridge import _parse_feature_flag
        for val in ("false", "0", "no", "FALSE", "NO", "False"):
            with patch.dict("os.environ", {"_TEST_FLAG": val}):
                assert _parse_feature_flag("_TEST_FLAG") is False

    def test_default_false_when_not_set(self):
        from src.core.rust_bridge import _parse_feature_flag
        import os
        env_without_flag = {k: v for k, v in os.environ.items() if k != "_TEST_FLAG"}
        with patch.dict("os.environ", env_without_flag, clear=True):
            assert _parse_feature_flag("_TEST_FLAG") is False

    def test_invalid_value_raises_value_error(self):
        from src.core.rust_bridge import _parse_feature_flag
        with patch.dict("os.environ", {"_TEST_FLAG": "maybe"}):
            with pytest.raises(ValueError, match="Invalid value"):
                _parse_feature_flag("_TEST_FLAG")

    def test_invalid_value_error_includes_env_name(self):
        from src.core.rust_bridge import _parse_feature_flag
        with patch.dict("os.environ", {"MY_FLAG": "bad"}):
            with pytest.raises(ValueError, match="MY_FLAG"):
                _parse_feature_flag("MY_FLAG")


# ---------------------------------------------------------------------------
# _try_import_rust
# ---------------------------------------------------------------------------

class TestTryImportRust:
    def setup_method(self):
        _reset_bridge()

    def test_import_success_returns_module(self):
        from src.core.rust_bridge import _try_import_rust
        mock_module = MagicMock()
        mock_module.__version__ = "0.1.0"
        with patch.dict("sys.modules", {"rust_core": mock_module}):
            result = _try_import_rust()
        assert result is mock_module

    def test_import_failure_returns_none(self):
        from src.core.rust_bridge import _try_import_rust
        # Setting sys.modules["rust_core"] = None forces ImportError on `import rust_core`
        with patch.dict("sys.modules", {"rust_core": None}):
            result = _try_import_rust()
        assert result is None


# ---------------------------------------------------------------------------
# _ensure_initialized
# ---------------------------------------------------------------------------

class TestEnsureInitialized:
    def setup_method(self):
        _reset_bridge()

    def test_all_flags_false_no_rust_import_attempted(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        with patch.dict("os.environ", {
            "USE_RUST_ORDERBOOK": "false",
            "USE_RUST_SIGNAL": "false",
            "USE_RUST_KILLSWITCH": "false",
        }):
            rb._ensure_initialized()
        assert rb._flags is not None
        assert rb._rust_module is None  # no flags → no import

    def test_idempotent_second_call(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        with patch.dict("os.environ", {
            "USE_RUST_ORDERBOOK": "false",
            "USE_RUST_SIGNAL": "false",
            "USE_RUST_KILLSWITCH": "false",
        }):
            rb._ensure_initialized()
            flags_ref = rb._flags
            rb._ensure_initialized()
            assert rb._flags is flags_ref  # same object → no re-init

    def test_invalid_flag_raises(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        with patch.dict("os.environ", {
            "USE_RUST_ORDERBOOK": "INVALID",
            "USE_RUST_SIGNAL": "false",
            "USE_RUST_KILLSWITCH": "false",
        }):
            with pytest.raises(ValueError):
                rb._ensure_initialized()

    def test_flag_true_but_import_fails_warns(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        with patch.dict("os.environ", {
            "USE_RUST_ORDERBOOK": "true",
            "USE_RUST_SIGNAL": "false",
            "USE_RUST_KILLSWITCH": "false",
        }):
            with patch("src.core.rust_bridge._try_import_rust", return_value=None):
                rb._ensure_initialized()
        assert rb._rust_module is None


# ---------------------------------------------------------------------------
# get_orderbook_class
# ---------------------------------------------------------------------------

class TestGetOrderbookClass:
    def setup_method(self):
        _reset_bridge()

    def test_returns_python_when_rust_disabled(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        from src.core.order_book import OrderBook
        assert rb.get_orderbook_class() is OrderBook

    def test_returns_rust_wrapper_when_enabled_and_module_present(self):
        import src.core.rust_bridge as rb
        from src.core.rust_bridge import RustOrderBookWrapper
        _reset_bridge()
        mock_module = MagicMock()
        mock_module.PyOrderBook = MagicMock()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = mock_module
        assert rb.get_orderbook_class() is RustOrderBookWrapper

    def test_falls_back_to_python_when_pyorderbook_missing(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_module = MagicMock(spec=[])  # no PyOrderBook attribute
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = mock_module
        from src.core.order_book import OrderBook
        assert rb.get_orderbook_class() is OrderBook

    def test_returns_python_when_module_none_despite_flag(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        from src.core.order_book import OrderBook
        assert rb.get_orderbook_class() is OrderBook


# ---------------------------------------------------------------------------
# get_spread_calculator
# ---------------------------------------------------------------------------

class TestGetSpreadCalculator:
    def setup_method(self):
        _reset_bridge()

    def test_returns_none_when_rust_disabled(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        assert rb.get_spread_calculator() is None

    def test_returns_rust_class_when_enabled(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_calc = MagicMock()
        mock_module = MagicMock()
        mock_module.PySpreadCalculator = mock_calc
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = mock_module
        assert rb.get_spread_calculator() is mock_calc

    def test_falls_back_when_calc_class_missing(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_module = MagicMock(spec=[])  # no PySpreadCalculator
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = mock_module
        assert rb.get_spread_calculator() is None

    def test_returns_none_when_module_none_despite_signal_flag(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        assert rb.get_spread_calculator() is None


# ---------------------------------------------------------------------------
# get_rust_kill_switch_functions
# ---------------------------------------------------------------------------

class TestGetRustKillSwitchFunctions:
    def setup_method(self):
        _reset_bridge()

    def test_returns_none_when_flag_false(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        assert rb.get_rust_kill_switch_functions() is None

    def test_returns_dict_with_all_symbols(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_module = MagicMock()
        mock_module.halt_local = MagicMock()
        mock_module.is_halted = MagicMock()
        mock_module.clear_halt = MagicMock()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = mock_module
        result = rb.get_rust_kill_switch_functions()
        assert result is not None
        assert "halt_local" in result
        assert "is_halted" in result
        assert "clear_halt" in result

    def test_includes_optional_killswitch_class(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_module = MagicMock()
        mock_module.halt_local = MagicMock()
        mock_module.is_halted = MagicMock()
        mock_module.clear_halt = MagicMock()
        mock_module.KillSwitch = MagicMock()
        mock_module.KillSwitchEvent = MagicMock()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = mock_module
        result = rb.get_rust_kill_switch_functions()
        assert "KillSwitch" in result
        assert "KillSwitchEvent" in result

    def test_returns_none_when_symbols_missing(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        mock_module = MagicMock(spec=["__version__"])  # no halt_local etc.
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = mock_module
        result = rb.get_rust_kill_switch_functions()
        assert result is None

    def test_returns_none_when_module_none(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = None
        assert rb.get_rust_kill_switch_functions() is None


# ---------------------------------------------------------------------------
# is_rust_* helpers
# ---------------------------------------------------------------------------

class TestIsRustEnabled:
    def setup_method(self):
        _reset_bridge()

    def test_orderbook_false_when_no_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        assert rb.is_rust_orderbook_enabled() is False

    def test_orderbook_false_when_flag_false(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = MagicMock()
        assert rb.is_rust_orderbook_enabled() is False

    def test_orderbook_true_when_flag_and_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = MagicMock()
        assert rb.is_rust_orderbook_enabled() is True

    def test_signal_false_when_no_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        assert rb.is_rust_signal_enabled() is False

    def test_killswitch_false_when_flag_false(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = MagicMock()
        assert rb.is_rust_killswitch_enabled() is False

    def test_killswitch_true_when_flag_and_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = MagicMock()
        assert rb.is_rust_killswitch_enabled() is True


# ---------------------------------------------------------------------------
# get_feature_flags
# ---------------------------------------------------------------------------

class TestGetFeatureFlags:
    def setup_method(self):
        _reset_bridge()

    def test_all_false_no_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": False, "USE_RUST_SIGNAL": False, "USE_RUST_KILLSWITCH": False}
        rb._rust_module = None
        flags = rb.get_feature_flags()
        assert flags == {
            "USE_RUST_ORDERBOOK": False,
            "USE_RUST_SIGNAL": False,
            "USE_RUST_KILLSWITCH": False,
        }

    def test_all_true_with_module(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = MagicMock()
        flags = rb.get_feature_flags()
        assert flags["USE_RUST_ORDERBOOK"] is True
        assert flags["USE_RUST_SIGNAL"] is True
        assert flags["USE_RUST_KILLSWITCH"] is True

    def test_flags_false_when_module_none_despite_enabled(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        rb._flags = {"USE_RUST_ORDERBOOK": True, "USE_RUST_SIGNAL": True, "USE_RUST_KILLSWITCH": True}
        rb._rust_module = None
        flags = rb.get_feature_flags()
        # Module unavailable → all reported as False
        assert flags["USE_RUST_ORDERBOOK"] is False
        assert flags["USE_RUST_SIGNAL"] is False
        assert flags["USE_RUST_KILLSWITCH"] is False

    def test_returns_all_three_keys(self):
        import src.core.rust_bridge as rb
        _reset_bridge()
        with patch.dict("os.environ", {
            "USE_RUST_ORDERBOOK": "false",
            "USE_RUST_SIGNAL": "false",
            "USE_RUST_KILLSWITCH": "false",
        }):
            flags = rb.get_feature_flags()
        assert set(flags.keys()) == {"USE_RUST_ORDERBOOK", "USE_RUST_SIGNAL", "USE_RUST_KILLSWITCH"}
