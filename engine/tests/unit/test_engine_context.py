"""Unit tests for EngineContext dataclass."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.api.server import EngineContext


class TestEngineContextHasShadowMode:
    def test_engine_context_has_shadow_mode(self):
        """EngineContext has a shadow_mode field that defaults to None."""
        ctx = EngineContext()
        assert hasattr(ctx, "shadow_mode")
        assert ctx.shadow_mode is None

    def test_engine_context_shadow_mode_assignable(self):
        """shadow_mode field accepts any value (duck-typed)."""
        ctx = EngineContext()
        mock_shadow = MagicMock()
        ctx.shadow_mode = mock_shadow
        assert ctx.shadow_mode is mock_shadow
