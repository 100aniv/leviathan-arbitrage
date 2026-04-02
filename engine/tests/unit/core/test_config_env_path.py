"""Tests for config.py root .env path — US-375.

US-375: engine/.env 삭제 + config.py 절대경로 수정
  - _load_dynaconf_defaults: _ENGINE_ROOT.parent / ".env" (repo root)
  - Settings.model_config: env_file = str(Path(...).parents[3] / ".env")
  - preflight.py: _check_env_sync() 삭제
"""
from __future__ import annotations

import importlib
import inspect
import textwrap
from pathlib import Path

import pytest

import src.core.config as config_module


# ---------------------------------------------------------------------------
# US-375-1: _load_dynaconf_defaults uses repo-root .env path
# ---------------------------------------------------------------------------

class TestDynaconfEnvPath:
    def test_dynaconf_uses_repo_root_env(self):
        """_load_dynaconf_defaults must reference _ENGINE_ROOT.parent / '.env' (repo root)."""
        src = inspect.getsource(config_module._load_dynaconf_defaults)
        # After fix: _ENGINE_ROOT.parent / ".env"
        assert "_ENGINE_ROOT.parent" in src, (
            "config.py _load_dynaconf_defaults still uses _ENGINE_ROOT / '.env' "
            "(engine-relative). Must use _ENGINE_ROOT.parent / '.env' (repo root)."
        )

    def test_dynaconf_does_not_use_engine_relative_env(self):
        """_load_dynaconf_defaults must NOT use engine-relative path."""
        src = inspect.getsource(config_module._load_dynaconf_defaults)
        # Ensure the old pattern is gone: _ENGINE_ROOT / ".env" without .parent
        lines = [l.strip() for l in src.splitlines() if "_env_file" in l and ".env" in l]
        for line in lines:
            assert ".parent" in line, (
                f"Found env_file line without .parent (engine-relative): {line!r}"
            )


# ---------------------------------------------------------------------------
# US-375-2: Settings.model_config uses absolute env_file path
# ---------------------------------------------------------------------------

class TestSettingsEnvFilePath:
    def test_settings_env_file_is_absolute_or_parents3(self):
        """Settings.model_config env_file must be absolute path (parents[3] / '.env')."""
        src = inspect.getsource(config_module.Settings)
        # After fix: env_file=str(Path(__file__).resolve().parents[3] / ".env")
        # Check that it's NOT a bare relative string ".env"
        assert 'env_file=".env"' not in src, (
            "Settings.model_config still uses relative env_file='.env'. "
            "Must use absolute path: str(Path(__file__).resolve().parents[3] / '.env')"
        )

    def test_settings_env_file_points_to_repo_root(self):
        """Settings model_config env_file must resolve to repo root .env."""
        src = inspect.getsource(config_module.Settings)
        assert "parents[3]" in src, (
            "Settings.model_config env_file must use parents[3] to reach repo root. "
            "config.py is at engine/src/core/config.py → parents[3] = repo root."
        )


# ---------------------------------------------------------------------------
# US-375-3: _ENGINE_ROOT points to engine/ directory
# ---------------------------------------------------------------------------

class TestEngineRoot:
    def test_engine_root_is_engine_dir(self):
        """_ENGINE_ROOT must be the engine/ directory."""
        engine_root = config_module._ENGINE_ROOT
        assert engine_root.name == "engine", (
            f"_ENGINE_ROOT should point to 'engine/' directory, got: {engine_root}"
        )

    def test_engine_root_parent_is_repo_root(self):
        """_ENGINE_ROOT.parent must be the repo root (contains both engine/ and dashboard/)."""
        repo_root = config_module._ENGINE_ROOT.parent
        assert (repo_root / "engine").is_dir(), (
            f"_ENGINE_ROOT.parent ({repo_root}) must be repo root containing engine/ dir"
        )

    def test_repo_root_env_file_exists(self):
        """Repo root .env must exist (single source of truth after US-375)."""
        repo_root = config_module._ENGINE_ROOT.parent
        env_path = repo_root / ".env"
        assert env_path.exists(), (
            f"Repo root .env not found at {env_path}. "
            "US-375 requires a single .env at repo root."
        )


# ---------------------------------------------------------------------------
# US-375-4: preflight.py _check_env_sync() deleted
# ---------------------------------------------------------------------------

class TestPreflightEnvSyncDeleted:
    def test_check_env_sync_removed_from_preflight(self):
        """preflight.py must NOT contain _check_env_sync (dead code after .env unification)."""
        try:
            from src.modes import preflight as preflight_mod
        except ImportError:
            pytest.skip("preflight module not importable")

        src = inspect.getsource(preflight_mod)
        assert "_check_env_sync" not in src, (
            "preflight.py still contains _check_env_sync(). "
            "After US-375 .env unification, this dead code must be deleted."
        )
