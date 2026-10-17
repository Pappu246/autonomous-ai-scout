"""Real Windows integration tests for bounded computer control.

These tests interact with live Win32 APIs and require an interactive
Windows desktop environment. On non-Windows platforms (e.g. Linux CI),
they skip rather than faking success.
"""

from __future__ import annotations

import base64
import os
import pytest

from autonomous_agent.computer.backend import WindowsBackend
from autonomous_agent.computer.policy import is_windows


pytestmark = pytest.mark.skipif(
    not is_windows(),
    reason="Windows integration tests require an interactive Windows desktop environment",
)


@pytest.fixture
def win_backend() -> WindowsBackend:
    return WindowsBackend()


def test_windows_integration_display_metrics(win_backend):
    info = win_backend.get_display_info()
    assert info.width > 0
    assert info.height > 0


def test_windows_integration_active_window(win_backend):
    w = win_backend.window_active()
    assert isinstance(w.handle, int)
    assert isinstance(w.title, str)


def test_windows_integration_window_list(win_backend):
    windows = win_backend.window_list()
    assert isinstance(windows, list)
    for w in windows:
        assert isinstance(w.handle, int)
        assert isinstance(w.title, str)


def test_windows_integration_screen_capture(win_backend):
    cap = win_backend.screen_capture(include_image=True)
    assert cap.get("captured") is True
    assert cap.get("format") == "png"
    assert "region" in cap
    raw = base64.b64decode(cap["image_base64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_windows_integration_clipboard_roundtrip(win_backend):
    if os.getenv("RUN_WINDOWS_CLIPBOARD_SMOKE") != "1":
        pytest.skip(
            "clipboard roundtrip changes the live desktop clipboard; "
            "set RUN_WINDOWS_CLIPBOARD_SMOKE=1 in an interactive Windows session to enable it"
        )
    sample = "autonomous_test_token_456"
    success = win_backend.clipboard_write(sample)
    assert success is True
    read_back = win_backend.clipboard_read()
    assert read_back == sample


def test_windows_integration_cursor_move(win_backend):
    res = win_backend.mouse_move(100, 100)
    assert res.get("success") is True
    assert res.get("x") == 100
    assert res.get("y") == 100
