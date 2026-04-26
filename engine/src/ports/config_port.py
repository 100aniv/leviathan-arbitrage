"""ConfigPort — Phase 7 config abstraction (2026-04-26).

Codex SUGGEST (codex-review-newly-added-ports-2026-04-26): runtime의 강한 결합을
ConfigPort로 해체. pipeline_init / bootstrap / runtime은 get_settings() / get_config
직접 호출 대신 ConfigPort에 의존.

구현체:
- engine/src/core/config_loader.py 모듈 (현재 — module-level 함수)
- 향후 ConfigServiceAdapter wrap (DI-friendly)
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConfigPort(Protocol):
    """Hexagonal port for engine config access.

    핵심 단일 메서드: dotpath get with default. 모든 호출은 engine.json + env var
    fallback을 통해 해결됨 (config_loader.py 정합).
    """

    def get(self, dotpath: str, default: Any = None) -> Any:
        """Configuration 조회 (dotpath, e.g. 'capital.tier').

        Returns engine.json 값 → env var → default 순.
        """
        ...

    def get_bool(self, name: str) -> bool:
        """Feature flag boolean 조회.

        engine.json.feature_flags[name] → env var (true/1/yes) → False.
        """
        ...
