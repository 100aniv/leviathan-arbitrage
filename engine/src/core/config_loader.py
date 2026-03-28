"""Unified config loader — config/trading.json 우선, .env fallback.

Usage:
    from src.core.config_loader import get_config
    val = get_config("strategy_filters.funding_zscore_threshold", default=-1)
    val = get_config("engine.log_level", default="INFO")

설정 우선순위: trading.json > 환경변수 > default
- 대시보드/텔레그램/오토튜너에서 trading.json 직접 수정 가능
- .env는 시크릿(API키/토큰/DB URL)만 보관
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_CONFIG_PATH = Path(os.environ.get(
    "TRADING_CONFIG_PATH",
    str(Path(__file__).parent.parent.parent / "config" / "trading.json"),
))

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(_CONFIG_PATH.read_text())
        logger.debug("config_loader.loaded", path=str(_CONFIG_PATH))
    except Exception as exc:
        logger.warning("config_loader.load_failed", path=str(_CONFIG_PATH), error=str(exc))
        _cache = {}
    return _cache


def reload() -> dict[str, Any]:
    """Force reload from disk (for hot-reload after dashboard/tuner changes)."""
    global _cache
    _cache = None
    return _load()


def get_config(dotpath: str, default: Any = None, env_key: str | None = None) -> Any:
    """Get config value by dot-path. Falls back to env var, then default.

    Args:
        dotpath: e.g. "strategy_filters.funding_zscore_threshold"
        default: fallback value if not found anywhere
        env_key: optional env var name override (for backward compat)

    Returns:
        Value from trading.json, or env var, or default.
    """
    data = _load()

    # Navigate dot-path in trading.json
    parts = dotpath.split(".")
    node = data
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = None
            break

    if node is not None:
        return node

    # Fallback to env var
    if env_key:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return _coerce(env_val, default)

    # Auto-derive env key from dotpath: strategy_filters.funding_zscore_threshold → FUNDING_ZSCORE_THRESHOLD
    auto_key = parts[-1].upper()
    env_val = os.environ.get(auto_key)
    if env_val is not None:
        return _coerce(env_val, default)

    return default


def _coerce(val: str, reference: Any) -> Any:
    """Coerce string env val to match reference type."""
    if reference is None:
        return val
    if isinstance(reference, bool):
        return val.lower() in ("true", "1", "yes")
    if isinstance(reference, int):
        try:
            return int(val)
        except ValueError:
            return int(float(val))
    if isinstance(reference, float):
        return float(val)
    return val
