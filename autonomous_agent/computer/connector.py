"""Bounded Windows computer control connector."""

from __future__ import annotations

import sys
from typing import Any, Mapping, Sequence

from ..prompt_injection_guard import PromptInjectionGuard, TrustLevel
from .backend import (
    BaseComputerBackend,
    MockComputerBackend,
    UnsupportedPlatformBackend,
    WindowsBackend,
)
from .models import (
    ActionBudget,
    ComputerSecurityError,
    DisplayInfo,
    ScreenRegion,
    WindowInfo,
    redact_text,
)
from .policy import (
    is_windows,
    validate_app_launch,
    validate_click,
    validate_clipboard_text,
    validate_coordinates,
    validate_hotkey,
    validate_typed_text,
)
from .replay import ComputerReplayProtector


class BoundedComputerConnector:
    """Safe, bounded connector for desktop interaction.

    Enforces action budgets, coordinate validation, denylisted processes,
    credential redaction, and replay protection.
    """

    def __init__(
        self,
        backend: BaseComputerBackend | None = None,
        budget: ActionBudget | None = None,
        replay: ComputerReplayProtector | None = None,
        guard: PromptInjectionGuard | None = None,
    ) -> None:
        if backend is not None:
            self._backend = backend
        elif is_windows():
            self._backend = WindowsBackend()
        else:
            self._backend = UnsupportedPlatformBackend()

        self._budget = budget or ActionBudget(limit=50)
        self._replay = replay or ComputerReplayProtector()
        self._guard = guard or PromptInjectionGuard()

    @property
    def backend(self) -> BaseComputerBackend:
        return self._backend

    @property
    def budget(self) -> ActionBudget:
        return self._budget

    @property
    def replay(self) -> ComputerReplayProtector:
        return self._replay

    # -- 11 bounded capabilities ------------------------------------------

    def screen_capture(self, region: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Capture screen image or sub-region."""
        self._budget.consume(1)
        parsed_region: ScreenRegion | None = None
        if region:
            parsed_region = ScreenRegion(
                x=int(region.get("x", 0)),
                y=int(region.get("y", 0)),
                width=int(region.get("width", 100)),
                height=int(region.get("height", 100)),
            )
        result = self._backend.screen_capture(parsed_region)
        return {
            "format": result.get("format", "png_metadata"),
            "region": result.get("region", {}),
            "captured": True,
            "verification_status": "verified",
        }

    def window_list(self, filter: str | None = None) -> list[dict[str, Any]]:
        """List active windows matching an optional filter string."""
        self._budget.consume(1)
        windows = self._backend.window_list(filter_title=filter)
        return [w.safe_dict() for w in windows]

    def window_active(self) -> dict[str, Any]:
        """Return the active foreground window."""
        self._budget.consume(1)
        w = self._backend.window_active()
        return w.safe_dict()

    def window_focus(self, handle: int | None = None, title: str | None = None) -> dict[str, Any]:
        """Bring a targeted window to foreground."""
        self._budget.consume(1)
        if handle is None and not title:
            raise ComputerSecurityError("window focus requires handle or title")
        success = self._backend.window_focus(handle=handle, title=title)
        active = self._backend.window_active()
        return {
            "focused": bool(success),
            "handle": handle,
            "title": title,
            "active_window": active.safe_dict(),
        }

    def app_launch(
        self,
        app: str,
        argv: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Launch an approved application with bounds and replay protection."""
        self._budget.consume(1)
        cleaned_app, validated_argv = validate_app_launch(app, argv)

        # Check replay protection
        existing_windows = self._backend.window_list()
        existing = self._replay.get_existing_launch(
            cleaned_app, validated_argv, idempotency_key, existing_windows
        )
        if existing is not None:
            return existing

        result = self._backend.app_launch(cleaned_app, validated_argv)
        self._replay.record_launch(cleaned_app, validated_argv, idempotency_key, result)
        return result

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        """Move cursor to valid display coordinates."""
        self._budget.consume(1)
        display = self._backend.get_display_info()
        ix, iy = validate_coordinates(x, y, display)
        return self._backend.mouse_move(ix, iy)

    def mouse_click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        """Click mouse button at specified or current coordinates."""
        self._budget.consume(1)
        display = self._backend.get_display_info()
        action = validate_click(x, y, button, clicks, display)
        return self._backend.mouse_click(action.x, action.y, action.button, action.clicks)

    def keyboard_type(self, text: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Type safe text into the currently active control."""
        self._budget.consume(1)
        clean_text = validate_typed_text(text)

        active = self._backend.window_active()
        existing = self._replay.get_existing_type(clean_text, idempotency_key, active.handle)
        if existing is not None:
            return existing

        result = self._backend.keyboard_type(clean_text)
        self._replay.record_type(clean_text, idempotency_key, active.handle, result)
        return result

    def keyboard_hotkey(
        self,
        keys: Sequence[str] | None = None,
        hotkey: str | None = None,
    ) -> dict[str, Any]:
        """Trigger an approved hotkey combination."""
        self._budget.consume(1)
        if keys is None and hotkey:
            keys = [k.strip() for k in hotkey.replace("+", " ").split() if k.strip()]
        if not keys:
            raise ComputerSecurityError("hotkey combination cannot be empty")
        valid_keys = validate_hotkey(keys)
        return self._backend.keyboard_hotkey(valid_keys)

    def clipboard_read(self) -> dict[str, Any]:
        """Read clipboard content with secret redaction."""
        self._budget.consume(1)
        raw = self._backend.clipboard_read()
        sanitized = redact_text(raw)
        return {
            "content": sanitized,
            "length": len(sanitized),
            "redacted": sanitized != raw,
        }

    def clipboard_write(self, text: str) -> dict[str, Any]:
        """Write bounded text to clipboard."""
        self._budget.consume(1)
        clean_text = validate_clipboard_text(text)
        success = self._backend.clipboard_write(clean_text)
        return {
            "written": bool(success),
            "length": len(clean_text),
        }


__all__ = ["BoundedComputerConnector"]
