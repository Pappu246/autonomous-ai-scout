"""Bounded Windows computer control domain.

Provides real, bounded Windows computer control through the canonical
digital agent lifecycle:
  Goal -> Intent -> Plan -> Capability Selection -> Authorization ->
  Execution -> Observation -> Verification -> Retry/Resume -> Result

Guarantees:
- Existing authorization, sandbox, and audit boundaries remain authoritative.
- Unsupported platforms fail closed.
- Side effects are approval-gated.
- No unrestricted shell or process execution.
- Action budgets and replay protection are enforced.
- Input dispatch alone never produces VERIFIED.
"""

from __future__ import annotations

from .backend import (
    BaseComputerBackend,
    MockComputerBackend,
    UnsupportedPlatformBackend,
    WindowsBackend,
)
from .connector import BoundedComputerConnector
from .models import (
    ActionBudget,
    ActionBudgetExceededError,
    ComputerError,
    ComputerReplayError,
    ComputerSecurityError,
    DisplayInfo,
    KeyAction,
    MouseAction,
    PlatformNotSupportedError,
    ScreenRegion,
    WindowInfo,
    redact_text,
)
from .observer import ComputerPostConditionObserver
from .policy import (
    DENYLISTED_APPS,
    FORBIDDEN_METACHARS,
    is_windows,
    validate_app_launch,
    validate_click,
    validate_clipboard_text,
    validate_coordinates,
    validate_hotkey,
    validate_typed_text,
)
from .replay import ComputerReplayProtector

__all__ = [
    "DENYLISTED_APPS",
    "FORBIDDEN_METACHARS",
    "ActionBudget",
    "ActionBudgetExceededError",
    "BaseComputerBackend",
    "BoundedComputerConnector",
    "ComputerError",
    "ComputerPostConditionObserver",
    "ComputerReplayError",
    "ComputerReplayProtector",
    "ComputerSecurityError",
    "DisplayInfo",
    "KeyAction",
    "MockComputerBackend",
    "MouseAction",
    "PlatformNotSupportedError",
    "ScreenRegion",
    "UnsupportedPlatformBackend",
    "WindowInfo",
    "WindowsBackend",
    "is_windows",
    "redact_text",
    "validate_app_launch",
    "validate_click",
    "validate_clipboard_text",
    "validate_coordinates",
    "validate_hotkey",
    "validate_typed_text",
]
