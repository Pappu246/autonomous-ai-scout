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
    validate_drag_path,
    validate_hotkey,
    validate_keypress,
    validate_scroll,
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

    def screen_capture(
        self,
        region: Mapping[str, Any] | None = None,
        *,
        include_image: bool = False,
    ) -> dict[str, Any]:
        """Capture screen metadata and optionally a real PNG payload."""
        self._budget.consume(1)
        parsed_region: ScreenRegion | None = None
        if region:
            parsed_region = ScreenRegion(
                x=int(region.get("x", 0)),
                y=int(region.get("y", 0)),
                width=int(region.get("width", 100)),
                height=int(region.get("height", 100)),
            )
        result = self._backend.screen_capture(parsed_region, include_image=include_image)
        safe = {
            "format": result.get("format", "png"),
            "media_type": result.get("media_type", "image/png"),
            "region": result.get("region", {}),
            "captured": bool(result.get("captured")),
            "byte_length": result.get("byte_length", 0),
            "verification_status": "verified" if result.get("captured") else "failed",
        }
        if include_image and isinstance(result.get("image_base64"), str):
            safe["image_base64"] = result["image_base64"]
        return safe

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

    def mouse_double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        return self.mouse_click(x, y, button, 2)

    def mouse_scroll(self, x: int, y: int, scroll_x: int = 0, scroll_y: int = 0) -> dict[str, Any]:
        self._budget.consume(1)
        display = self._backend.get_display_info()
        ix, iy = validate_coordinates(x, y, display)
        sx, sy = validate_scroll(scroll_x, scroll_y)
        return self._backend.mouse_scroll(ix, iy, sx, sy)

    def mouse_drag(
        self,
        path: Sequence[tuple[int, int]],
        button: str = "left",
        duration_ms: int = 250,
    ) -> dict[str, Any]:
        self._budget.consume(1)
        display = self._backend.get_display_info()
        normalized = validate_drag_path(path, display)
        if button not in {"left", "right", "middle"}:
            raise ComputerSecurityError(f"unsupported mouse button: {button}")
        return self._backend.mouse_drag(
            normalized,
            button,
            max(0, min(int(duration_ms), 5000)),
        )

    def keyboard_press(self, keys: Sequence[str]) -> dict[str, Any]:
        self._budget.consume(1)
        return self._backend.keyboard_press(validate_keypress(keys))

    def wait(self, milliseconds: int = 500) -> dict[str, Any]:
        self._budget.consume(1)
        return self._backend.wait(max(0, min(int(milliseconds), 10_000)))

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

    def execute_cua_action(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Execute one structured computer-tool action through this connector."""
        action_type = str(action.get("type", "")).strip().lower()
        if not action_type:
            raise ComputerSecurityError("computer action type is required")
        mutating = action_type in {"click", "double_click", "drag", "type", "keypress"}
        if mutating and not approved:
            raise ComputerSecurityError("state-changing computer action requires explicit approval")

        if action_type == "screenshot":
            return self.screen_capture(include_image=True)
        if action_type == "click":
            return self.mouse_click(int(action["x"]), int(action["y"]), str(action.get("button", "left")), 1)
        if action_type == "double_click":
            return self.mouse_click(int(action["x"]), int(action["y"]), str(action.get("button", "left")), 2)
        if action_type == "move":
            return self.mouse_move(int(action["x"]), int(action["y"]))
        if action_type == "scroll":
            return self.mouse_scroll(
                int(action["x"]),
                int(action["y"]),
                int(action.get("scroll_x", 0)),
                int(action.get("scroll_y", 0)),
            )
        if action_type == "drag":
            raw_path = action.get("path")
            if not isinstance(raw_path, (list, tuple)):
                raise ComputerSecurityError("drag action requires a path")
            path = tuple(
                (int(point["x"]), int(point["y"]))
                if isinstance(point, Mapping)
                else (int(point[0]), int(point[1]))
                for point in raw_path
            )
            return self.mouse_drag(path, str(action.get("button", "left")), int(action.get("duration_ms", 250)))
        if action_type == "type":
            return self.keyboard_type(str(action.get("text", "")))
        if action_type == "keypress":
            raw_keys = action.get("keys")
            if not isinstance(raw_keys, (list, tuple)):
                raise ComputerSecurityError("keypress action requires a keys array")
            return self.keyboard_press(tuple(str(key) for key in raw_keys))
        if action_type == "wait":
            duration = action.get("duration", action.get("milliseconds", 500))
            if isinstance(duration, float) and duration <= 10:
                duration = int(duration * 1000)
            return self.wait(int(duration))
        raise ComputerSecurityError(f"unsupported computer action type: {action_type}")

    def computer_use(
        self,
        task: str,
        *,
        approved: bool = False,
        max_turns: int = 20,
    ) -> dict[str, Any]:
        """Run a model-directed native computer-use task through this connector."""
        if not approved:
            return {
                "state": "requires_approval",
                "reason": "native computer-use can generate state-changing UI actions",
                "turns": 0,
                "actions": 0,
            }
        from .ai_controller import OpenAIComputerUseController
        result = OpenAIComputerUseController(self).run(
            task,
            approved=True,
            max_turns=max_turns,
        )
        return result.safe_dict()

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
