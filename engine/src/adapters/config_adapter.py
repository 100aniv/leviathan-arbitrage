"""ConfigAdapter — Phase 7 ConfigPort 구현체 (2026-04-27).

src/core/config_loader.py 모듈 함수를 인스턴스 메서드로 wrap.
DI-friendly: ConfigPort 인터페이스 통해 inject 가능.
"""
from __future__ import annotations

from typing import Any

from src.core.config_loader import get_bool_flag, get_config


class ConfigAdapter:
    """Concrete ConfigPort impl — module function wrapper.

    Production 사용: bootstrap에서 1회 인스턴스 생성 → runtime에 inject.
    """

    def get(self, dotpath: str, default: Any = None) -> Any:
        return get_config(dotpath, default)

    def get_bool(self, name: str) -> bool:
        return get_bool_flag(name)
