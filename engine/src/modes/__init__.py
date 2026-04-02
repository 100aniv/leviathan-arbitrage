"""LEVIATHAN Engine Modes — Paper, Live Gate."""
from __future__ import annotations

from src.modes.base import BaseMode, BaseModeStats
from src.modes.live_gate import LiveGate, LiveGateCheck, LiveGateResult
from src.modes.paper import PaperMode, ShadowMode  # ShadowMode is a backward-compat alias

__all__ = [
    "BaseMode",
    "BaseModeStats",
    "LiveGate",
    "LiveGateCheck",
    "LiveGateResult",
    "PaperMode",
    "ShadowMode",  # backward-compat alias
]
