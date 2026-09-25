"""Platform-independent policy, bounds, and security validation for computer control."""

from __future__ import annotations

import os
import re
import sys
from pathlib import PurePath, PureWindowsPath
from typing import Sequence

from .models import (
    ComputerSecurityError,
    DisplayInfo,
    KeyAction,
    MouseAction,
    ScreenRegion,
    redact_text,
)


DENYLISTED_APPS: frozenset[str] = frozenset({
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "bash.exe",
    "sh",
    "sh.exe",
    "zsh",
    "zsh.exe",
    "wsl",
    "wsl.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    "mshta",
    "mshta.exe",
    "certutil",
    "certutil.exe",
    "reg",
    "reg.exe",
    "regedit",
    "regedit.exe",
    "rundll32",
    "rundll32.exe",
    "installutil",
    "installutil.exe",
    "bitsadmin",
    "bitsadmin.exe",
    "vssadmin",
    "vssadmin.exe",
    "format",
    "format.com",
    "diskpart",
    "diskpart.exe",
    "curl",
    "curl.exe",
    "wget",
    "wget.exe",
    "taskkill",
    "taskkill.exe",
    "shutdown",
    "shutdown.exe",
})

FORBIDDEN_METACHARS: frozenset[str] = frozenset({
    ";", "&", "|", "`", "$", ">", "<", "\n", "\r", "\x00",
})

ALLOWED_BUTTONS: frozenset[str] = frozenset({"left", "right", "middle"})

ALLOWED_KEYS: frozenset[str] = frozenset({
    "ctrl", "control", "alt", "shift", "win", "windows", "enter", "return",
    "tab", "esc", "escape", "space", "backspace", "delete", "insert",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "capslock", "numlock", "scrolllock", "printscreen", "pause",
    *(chr(c) for c in range(ord("a"), ord("z") + 1)),
    *(str(d) for d in range(10)),
    *(f"f{i}" for i in range(1, 25)),
})

BLOCKED_HOTKEYS: frozenset[frozenset[str]] = frozenset({
    frozenset({"ctrl", "alt", "delete"}),
    frozenset({"control", "alt", "delete"}),
    frozenset({"win", "l"}),
    frozenset({"windows", "l"}),
})

MAX_TEXT_LENGTH = 4096
MAX_CLIPBOARD_LENGTH = 65536
MAX_ARGV_COUNT = 50
MAX_ARG_LENGTH = 2048

_CREDENTIAL_INSPECTION = re.compile(
    r"(?i)(?:api[_-]?key|(?:access[_-]?)?token|authorization|password|passwd|secret|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]\s*\S+"
)
_BEARER_INSPECTION = re.compile(r"(?i)bearer\s+\S+")


def is_windows() -> bool:
    """Return True only on Windows operating systems."""
    return sys.platform == "win32"


def validate_app_launch(app: str, argv: Sequence[str] = ()) -> tuple[str, tuple[str, ...]]:
    """Validate process launch arguments against denylists, metacharacters, and bounds."""
    if not isinstance(app, str) or not app.strip():
        raise ComputerSecurityError("application executable cannot be empty")
    cleaned_app = app.strip()

    for ch in FORBIDDEN_METACHARS:
        if ch in cleaned_app:
            raise ComputerSecurityError(
                f"forbidden metacharacter in application name: {repr(ch)}"
            )

    # Check basename (handling both POSIX and Windows separators)
    basename = PureWindowsPath(cleaned_app).name.lower()
    if not basename:
        basename = PurePath(cleaned_app).name.lower()
    if basename in DENYLISTED_APPS:
        raise ComputerSecurityError(
            f"application is denylisted for security: {basename}"
        )

    if not isinstance(argv, (list, tuple)):
        raise ComputerSecurityError("argv must be a list or tuple of strings")
    if len(argv) > MAX_ARGV_COUNT:
        raise ComputerSecurityError(
            f"argv argument count exceeds bound: {len(argv)} > {MAX_ARGV_COUNT}"
        )

    validated_args: list[str] = []
    for arg in argv:
        if not isinstance(arg, str):
            raise ComputerSecurityError("argv items must be strings")
        if len(arg) > MAX_ARG_LENGTH:
            raise ComputerSecurityError(
                f"argv item exceeds max length {MAX_ARG_LENGTH}: {len(arg)}"
            )
        for ch in FORBIDDEN_METACHARS:
            if ch in arg:
                raise ComputerSecurityError(
                    f"forbidden metacharacter in argv argument: {repr(ch)}"
                )
        validated_args.append(arg)

    return cleaned_app, tuple(validated_args)


def validate_coordinates(x: int, y: int, display: DisplayInfo) -> tuple[int, int]:
    """Ensure coordinates are within physical screen boundaries."""
    try:
        ix = int(x)
        iy = int(y)
    except (TypeError, ValueError) as exc:
        raise ComputerSecurityError(f"coordinates must be integers: {x}, {y}") from exc

    if ix < 0 or iy < 0:
        raise ComputerSecurityError(
            f"coordinates cannot be negative: ({ix}, {iy})"
        )
    if display.width > 0 and display.height > 0:
        if ix > display.width or iy > display.height:
            raise ComputerSecurityError(
                f"coordinates out of screen bounds: ({ix}, {iy}) exceeds display ({display.width}, {display.height})"
            )
    return ix, iy


def validate_click(
    x: int | None,
    y: int | None,
    button: str,
    clicks: int,
    display: DisplayInfo,
) -> MouseAction:
    """Validate mouse click parameters."""
    btn = str(button).strip().lower()
    if btn not in ALLOWED_BUTTONS:
        raise ComputerSecurityError(f"unsupported mouse button: {button}")

    try:
        n_clicks = int(clicks)
    except (TypeError, ValueError) as exc:
        raise ComputerSecurityError(f"clicks must be an integer: {clicks}") from exc

    if not 1 <= n_clicks <= 3:
        raise ComputerSecurityError(
            f"click count is out of safe range (1..3): {n_clicks}"
        )

    if x is not None and y is not None:
        ix, iy = validate_coordinates(x, y, display)
    else:
        ix, iy = 0, 0

    return MouseAction("click", ix, iy, btn, n_clicks)


def validate_typed_text(text: str) -> str:
    """Validate text to be typed, preventing credential leakage."""
    if not isinstance(text, str):
        raise ComputerSecurityError("text to type must be a string")
    if len(text) > MAX_TEXT_LENGTH:
        raise ComputerSecurityError(
            f"typed text exceeds max allowed length: {len(text)} > {MAX_TEXT_LENGTH}"
        )
    if _CREDENTIAL_INSPECTION.search(text) or _BEARER_INSPECTION.search(text):
        raise ComputerSecurityError(
            "credentials must never leak through typed text"
        )
    return text


def validate_hotkey(keys: Sequence[str]) -> tuple[str, ...]:
    """Validate hotkey combination against allowlist and blocked patterns."""
    if not isinstance(keys, (list, tuple)) or not keys:
        raise ComputerSecurityError("hotkey must be a non-empty sequence of keys")

    norm_keys: list[str] = []
    for k in keys:
        if not isinstance(k, str) or not k.strip():
            raise ComputerSecurityError("hotkey key must be a non-empty string")
        nk = k.strip().lower()
        if nk not in ALLOWED_KEYS:
            raise ComputerSecurityError(f"unrecognized or unsafe key in hotkey: {k}")
        norm_keys.append(nk)

    key_set = frozenset(norm_keys)
    for blocked in BLOCKED_HOTKEYS:
        if blocked.issubset(key_set):
            raise ComputerSecurityError(
                f"hotkey combination is blocked by security policy: {list(keys)}"
            )

    return tuple(norm_keys)


def validate_clipboard_text(text: str) -> str:
    """Validate text to write to clipboard."""
    if not isinstance(text, str):
        raise ComputerSecurityError("clipboard text must be a string")
    if len(text) > MAX_CLIPBOARD_LENGTH:
        raise ComputerSecurityError(
            f"clipboard text exceeds max allowed length: {len(text)} > {MAX_CLIPBOARD_LENGTH}"
        )
    if _CREDENTIAL_INSPECTION.search(text) or _BEARER_INSPECTION.search(text):
        raise ComputerSecurityError(
            "credentials must never leak through clipboard write"
        )
    return text


__all__ = [
    "ALLOWED_BUTTONS",
    "ALLOWED_KEYS",
    "BLOCKED_HOTKEYS",
    "DENYLISTED_APPS",
    "FORBIDDEN_METACHARS",
    "MAX_ARGV_COUNT",
    "MAX_ARG_LENGTH",
    "MAX_CLIPBOARD_LENGTH",
    "MAX_TEXT_LENGTH",
    "is_windows",
    "validate_app_launch",
    "validate_click",
    "validate_clipboard_text",
    "validate_coordinates",
    "validate_hotkey",
    "validate_typed_text",
]
