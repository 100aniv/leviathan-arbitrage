"""Unified config loader — engine.json is the sole runtime config source.

.env에는 시크릿(API키/토큰/DB URL/비밀번호)만 보관.
모든 비시크릿 설정은 engine.json에서 get_config()로 접근.

설정 우선순위: engine.json > 환경변수 > default
(WS-1: trading.json deep-merge 제거 — 키 leak 방지)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent  # engine/
_ENGINE_JSON = _ENGINE_ROOT / "config" / "engine.json"

_cache: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge: override wins for scalar values, recurse for dicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache

    merged: dict[str, Any] = {}

    # engine.json is the sole config source (WS-1: trading.json removed)
    try:
        merged = json.loads(_ENGINE_JSON.read_text())
        logger.debug("config_loader.loaded_engine", path=str(_ENGINE_JSON))
    except Exception as exc:
        logger.warning("config_loader.engine_load_failed", path=str(_ENGINE_JSON), error=str(exc))

    _cache = merged
    return _cache


def reload() -> dict[str, Any]:
    """Force reload from disk (for hot-reload after dashboard/tuner changes)."""
    global _cache
    _cache = None
    return _load()


def get_config(dotpath: str, default: Any = None, env_key: str | None = None) -> Any:
    """비시크릿 설정 값을 dot-path로 조회. 환경변수 fallback, 그다음 default.

    Args:
        dotpath: e.g. "strategy_filters.funding_zscore_threshold"
        default: 어디서도 못 찾으면 반환할 기본값
        env_key: 명시적 env var 이름 (하위 호환용)

    Returns:
        engine.json 또는 trading.json 값, 또는 env var, 또는 default.
    """
    data = _load()

    # Navigate dot-path
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

    # Fallback to explicit env var
    if env_key:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return _coerce(env_val, default)

    # Auto-derive env key from dotpath
    auto_key = parts[-1].upper()
    env_val = os.environ.get(auto_key)
    if env_val is not None:
        return _coerce(env_val, default)

    return default


def _coerce(val: str, reference: Any) -> Any:
    """string env var 값을 reference 타입으로 강제 변환."""
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
