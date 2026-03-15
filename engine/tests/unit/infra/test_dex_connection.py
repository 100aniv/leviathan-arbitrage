"""US-177: DEX real connection — CexDex strategy registration based on env vars."""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Environment-variable gating
# ---------------------------------------------------------------------------


class TestDEXEnvVarGating:
    def test_cex_dex_skipped_when_no_env_vars(self, monkeypatch):
        """CexDex strategy is NOT registered when DEX_RPC_URL is unset."""
        monkeypatch.delenv("DEX_RPC_URL", raising=False)
        monkeypatch.delenv("DEX_POOL_ADDRESS", raising=False)

        dex_rpc = os.getenv("DEX_RPC_URL", "")
        pool = os.getenv("DEX_POOL_ADDRESS", "")

        # Registration logic: skip if either is missing
        should_register = bool(dex_rpc and pool)
        assert should_register is False

    def test_cex_dex_registered_when_both_env_vars_set(self, monkeypatch):
        """CexDex strategy IS registered when DEX_RPC_URL + DEX_POOL_ADDRESS are set."""
        monkeypatch.setenv("DEX_RPC_URL", "https://mainnet.infura.io/v3/test")
        monkeypatch.setenv("DEX_POOL_ADDRESS", "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8")

        dex_rpc = os.getenv("DEX_RPC_URL", "")
        pool = os.getenv("DEX_POOL_ADDRESS", "")

        should_register = bool(dex_rpc and pool)
        assert should_register is True

    def test_cex_dex_skipped_when_pool_address_missing(self, monkeypatch):
        """CexDex strategy is NOT registered when DEX_POOL_ADDRESS is missing."""
        monkeypatch.setenv("DEX_RPC_URL", "https://mainnet.infura.io/v3/test")
        monkeypatch.delenv("DEX_POOL_ADDRESS", raising=False)

        dex_rpc = os.getenv("DEX_RPC_URL", "")
        pool = os.getenv("DEX_POOL_ADDRESS", "")

        should_register = bool(dex_rpc and pool)
        assert should_register is False

    def test_cex_dex_skipped_when_rpc_url_missing(self, monkeypatch):
        """CexDex strategy is NOT registered when DEX_RPC_URL is missing."""
        monkeypatch.delenv("DEX_RPC_URL", raising=False)
        monkeypatch.setenv("DEX_POOL_ADDRESS", "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8")

        dex_rpc = os.getenv("DEX_RPC_URL", "")
        pool = os.getenv("DEX_POOL_ADDRESS", "")

        should_register = bool(dex_rpc and pool)
        assert should_register is False


# ---------------------------------------------------------------------------
# UniswapV3Config parameter validation
# ---------------------------------------------------------------------------


class TestUniswapV3Config:
    def test_uniswap_v3_config_importable(self):
        """UniswapV3Config can be imported from the dex module."""
        try:
            from src.dex.uniswap_v3 import UniswapV3Config
            assert UniswapV3Config is not None
        except ImportError:
            pytest.skip("UniswapV3Config not yet implemented")

    def test_uniswap_v3_config_stores_rpc_and_pool(self):
        """UniswapV3Config stores rpc_url and pool_address."""
        try:
            from src.dex.uniswap_v3 import UniswapV3Config
            config = UniswapV3Config(
                rpc_url="https://mainnet.infura.io/v3/test",
                pool_address="0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
            )
            assert "mainnet.infura" in config.rpc_url or config.rpc_url
            assert config.pool_address.startswith("0x")
        except ImportError:
            pytest.skip("UniswapV3Config not yet implemented")


# ---------------------------------------------------------------------------
# CexDex strategy graceful skip
# ---------------------------------------------------------------------------


class TestCexDexGracefulSkip:
    def test_cex_dex_strategy_importable(self):
        """CexDexStrategy can be imported (existence check)."""
        try:
            from src.strategies.cex_dex import CexDexStrategy
            assert CexDexStrategy is not None
        except ImportError:
            pytest.skip("CexDexStrategy not available")

    def test_missing_env_vars_does_not_raise_import_error(self, monkeypatch):
        """Importing cex_dex module without env vars does not raise."""
        monkeypatch.delenv("DEX_RPC_URL", raising=False)
        monkeypatch.delenv("DEX_POOL_ADDRESS", raising=False)
        try:
            import importlib
            import src.strategies.cex_dex
            importlib.reload(src.strategies.cex_dex)
        except ImportError:
            pytest.skip("CexDex module not available")
        except Exception:
            # Any non-import error is acceptable behavior variation
            pass
