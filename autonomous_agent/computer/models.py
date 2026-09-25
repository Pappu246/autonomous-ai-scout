"""Core models, exceptions, action budgets, and redaction for bounded computer control."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


class ComputerError(Exception):
    """Base exception for computer domain operations."""


class ComputerSecurityError(ComputerError):
    """Raised when an action violates safety boundaries or policy."""


class PlatformNotSupportedError(ComputerError):
    """Raised when computer control is invoked on an unsupported operating system."""


class ActionBudgetExceededError(ComputerError):
    """Raised when an operation attempts to exceed the allocated action budget."""


class ComputerReplayError(ComputerError):
    """Raised when a non-idempotent or replayed mutating action is detected."""


@dataclass(frozen=True)
class WindowInfo:
    """Bounded, safe representation of an OS window."""

    handle: int
    title: str
    process_id: int = 0
    process_name: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    is_active: bool = False
    is_visible: bool = True

    def safe_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "bounds": {
                "left": self.bounds[0],
                "top": self.bounds[1],
                "right": self.bounds[2],
                "bottom": self.bounds[3],
                "width": max(0, self.bounds[2] - self.bounds[0]),
                "height": max(0, self.bounds[3] - self.bounds[1]),
            },
            "is_active": self.is_active,
            "is_visible": self.is_visible,
        }


@dataclass(frozen=True)
class DisplayInfo:
    """Display metrics and bounds."""

    width: int
    height: int
    virtual_width: int = 0
    virtual_height: int = 0
    dpi_scale: float = 1.0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "virtual_width": self.virtual_width or self.width,
            "virtual_height": self.virtual_height or self.height,
            "dpi_scale": self.dpi_scale,
        }


@dataclass(frozen=True)
class ScreenRegion:
    """Sub-region of display for screen capture."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ComputerSecurityError("region coordinates cannot be negative")
        if self.width <= 0 or self.height <= 0:
            raise ComputerSecurityError("region width and height must be positive")


@dataclass(frozen=True)
class MouseAction:
    """Validated mouse interaction record."""

    action: str  # "move", "click"
    x: int
    y: int
    button: str = "left"
    clicks: int = 1


@dataclass(frozen=True)
class KeyAction:
    """Validated keyboard interaction record."""

    action: str  # "type", "hotkey"
    text: str = ""
    keys: tuple[str, ...] = ()


class ActionBudget:
    """Enforces upper bounds on computer actions per execution run.

    Allows up to ``limit`` total consumed actions. The final permitted
    action is allowed when ``used + count == limit``.
    """

    def __init__(self, limit: int = 50) -> None:
        if limit <= 0:
            raise ValueError("action budget limit must be positive")
        self.limit = int(limit)
        self.used = 0

    def consume(self, count: int = 1) -> None:
        """Consume action budget units.

        Rejects only when attempted usage exceeds the limit.
        """
        if count <= 0:
            raise ValueError("action count must be positive")
        if self.used + count > self.limit:
            raise ActionBudgetExceededError(
                f"action budget exceeded: attempted {self.used + count}, limit is {self.limit}"
            )
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def reset(self) -> None:
        self.used = 0


_CREDENTIAL_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|(?:access[_-]?)?token|authorization|password|passwd|secret|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]\s*(?:bearer\s+)?)\S+"
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)\S+")
REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """Strip credential patterns from text without relying on nonexistent regex capture groups."""
    if not isinstance(text, str):
        return text
    b = _BEARER_PATTERN.sub(r"\1" + REDACTED, text)
    return _CREDENTIAL_PATTERN.sub(r"\1" + REDACTED, b)


__all__ = [
    "ActionBudget",
    "ActionBudgetExceededError",
    "ComputerError",
    "ComputerReplayError",
    "ComputerSecurityError",
    "DisplayInfo",
    "KeyAction",
    "MouseAction",
    "PlatformNotSupportedError",
    "ScreenRegion",
    "WindowInfo",
    "redact_text",
]
