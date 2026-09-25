"""Windows and mock backends for bounded computer control."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Any, Sequence

from .models import (
    DisplayInfo,
    PlatformNotSupportedError,
    ScreenRegion,
    WindowInfo,
)
from .policy import is_windows


class BaseComputerBackend(ABC):
    """Abstract interface for OS-level computer interactions."""

    @abstractmethod
    def get_display_info(self) -> DisplayInfo: ...

    @abstractmethod
    def screen_capture(self, region: ScreenRegion | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def window_list(self, filter_title: str | None = None) -> list[WindowInfo]: ...

    @abstractmethod
    def window_active(self) -> WindowInfo: ...

    @abstractmethod
    def window_focus(self, handle: int | None = None, title: str | None = None) -> bool: ...

    @abstractmethod
    def app_launch(self, app: str, argv: Sequence[str] = ()) -> dict[str, Any]: ...

    @abstractmethod
    def mouse_move(self, x: int, y: int) -> dict[str, Any]: ...

    @abstractmethod
    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]: ...

    @abstractmethod
    def keyboard_type(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def keyboard_hotkey(self, keys: Sequence[str]) -> dict[str, Any]: ...

    @abstractmethod
    def clipboard_read(self) -> str: ...

    @abstractmethod
    def clipboard_write(self, text: str) -> bool: ...


class UnsupportedPlatformBackend(BaseComputerBackend):
    """Backend used on non-Windows platforms. Always fails closed."""

    def __init__(self, platform_name: str | None = None) -> None:
        self._platform = platform_name or sys.platform

    def _fail_closed(self) -> None:
        raise PlatformNotSupportedError(
            f"Windows computer control is not supported on platform {self._platform!r}; failing closed"
        )

    def get_display_info(self) -> DisplayInfo:
        self._fail_closed()
        return DisplayInfo(0, 0)

    def screen_capture(self, region: ScreenRegion | None = None) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def window_list(self, filter_title: str | None = None) -> list[WindowInfo]:
        self._fail_closed()
        return []

    def window_active(self) -> WindowInfo:
        self._fail_closed()
        return WindowInfo(0, "")

    def window_focus(self, handle: int | None = None, title: str | None = None) -> bool:
        self._fail_closed()
        return False

    def app_launch(self, app: str, argv: Sequence[str] = ()) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def keyboard_type(self, text: str) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def keyboard_hotkey(self, keys: Sequence[str]) -> dict[str, Any]:
        self._fail_closed()
        return {}

    def clipboard_read(self) -> str:
        self._fail_closed()
        return ""

    def clipboard_write(self, text: str) -> bool:
        self._fail_closed()
        return False


class WindowsBackend(BaseComputerBackend):
    """Real Windows backend using stdlib ctypes and Win32 APIs only."""

    def __init__(self) -> None:
        if not is_windows():
            raise PlatformNotSupportedError("WindowsBackend requires a Windows operating system")
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._kernel32 = ctypes.windll.kernel32

    def get_display_info(self) -> DisplayInfo:
        w = self._user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = self._user32.GetSystemMetrics(1)  # SM_CYSCREEN
        vw = self._user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = self._user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        return DisplayInfo(
            width=int(w),
            height=int(h),
            virtual_width=int(vw or w),
            virtual_height=int(vh or h),
        )

    def screen_capture(self, region: ScreenRegion | None = None) -> dict[str, Any]:
        display = self.get_display_info()
        x = region.x if region else 0
        y = region.y if region else 0
        w = region.width if region else display.width
        h = region.height if region else display.height

        # Minimal bitmap capture via GDI
        hdc_screen = self._user32.GetDC(0)
        hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
        hbm = self._gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        hbm_old = self._gdi32.SelectObject(hdc_mem, hbm)

        # SRCCOPY = 0x00CC0020
        self._gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020)

        # Cleanup GDI handles
        self._gdi32.SelectObject(hdc_mem, hbm_old)
        self._gdi32.DeleteObject(hbm)
        self._gdi32.DeleteDC(hdc_mem)
        self._user32.ReleaseDC(0, hdc_screen)

        return {
            "format": "png_metadata",
            "region": {"x": x, "y": y, "width": w, "height": h},
            "captured": True,
        }

    def window_list(self, filter_title: str | None = None) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        wintypes = self._wintypes
        ctypes = self._ctypes

        def enum_handler(hwnd: int, lparam: int) -> bool:
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
            if filter_title and filter_title.lower() not in title.lower():
                return True

            rect = wintypes.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            bounds = (rect.left, rect.top, rect.right, rect.bottom)
            active_hwnd = self._user32.GetForegroundWindow()

            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            windows.append(
                WindowInfo(
                    handle=hwnd,
                    title=title,
                    process_id=int(pid.value),
                    bounds=bounds,
                    is_active=(hwnd == active_hwnd),
                    is_visible=True,
                )
            )
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumWindows(WNDENUMPROC(enum_handler), 0)
        return windows

    def window_active(self) -> WindowInfo:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return WindowInfo(0, "None", is_active=False)
        length = self._user32.GetWindowTextLengthW(hwnd)
        buff = self._ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buff, length + 1)

        rect = self._wintypes.RECT()
        self._user32.GetWindowRect(hwnd, self._ctypes.byref(rect))
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))

        return WindowInfo(
            handle=hwnd,
            title=buff.value,
            process_id=int(pid.value),
            bounds=(rect.left, rect.top, rect.right, rect.bottom),
            is_active=True,
            is_visible=True,
        )

    def window_focus(self, handle: int | None = None, title: str | None = None) -> bool:
        target_hwnd = handle
        if target_hwnd is None and title:
            for w in self.window_list(filter_title=title):
                target_hwnd = w.handle
                break
        if not target_hwnd:
            return False
        self._user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
        return bool(self._user32.SetForegroundWindow(target_hwnd))

    def app_launch(self, app: str, argv: Sequence[str] = ()) -> dict[str, Any]:
        """Launch process using ONLY ONE Popen call site, with shell=False."""
        cmd = [app, *argv]
        proc = subprocess.Popen(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            "pid": proc.pid,
            "app": app,
            "argv": list(argv),
            "launched": True,
        }

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        self._user32.SetCursorPos(int(x), int(y))
        return {"action": "move", "x": x, "y": y, "success": True}

    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self.mouse_move(x, y)
        down_flag = 0x0002 if button == "left" else (0x0008 if button == "right" else 0x0020)
        up_flag = 0x0004 if button == "left" else (0x0010 if button == "right" else 0x0040)
        for _ in range(clicks):
            self._user32.mouse_event(down_flag, 0, 0, 0, 0)
            self._user32.mouse_event(up_flag, 0, 0, 0, 0)
        return {
            "action": "click",
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "success": True,
        }

    def keyboard_type(self, text: str) -> dict[str, Any]:
        # Send characters via SendInput or keybd_event
        for ch in text:
            vk = self._user32.VkKeyScanW(ord(ch))
            if vk != -1:
                code = vk & 0xFF
                shift = (vk >> 8) & 1
                if shift:
                    self._user32.keybd_event(0x10, 0, 0, 0)  # VK_SHIFT down
                self._user32.keybd_event(code, 0, 0, 0)
                self._user32.keybd_event(code, 0, 2, 0)  # KEYEVENTF_KEYUP = 2
                if shift:
                    self._user32.keybd_event(0x10, 0, 2, 0)
        return {"action": "type", "length": len(text), "success": True}

    def keyboard_hotkey(self, keys: Sequence[str]) -> dict[str, Any]:
        key_map = {
            "ctrl": 0x11, "control": 0x11,
            "alt": 0x12,
            "shift": 0x10,
            "win": 0x5B, "windows": 0x5B,
            "enter": 0x0D, "return": 0x0D,
            "tab": 0x09,
            "esc": 0x1B, "escape": 0x1B,
            "space": 0x20,
            "backspace": 0x08,
            "delete": 0x2E,
        }
        vk_codes: list[int] = []
        for k in keys:
            lk = k.lower()
            if lk in key_map:
                vk_codes.append(key_map[lk])
            elif len(lk) == 1:
                vk_codes.append(ord(lk.upper()))

        for code in vk_codes:
            self._user32.keybd_event(code, 0, 0, 0)
        for code in reversed(vk_codes):
            self._user32.keybd_event(code, 0, 2, 0)

        return {"action": "hotkey", "keys": list(keys), "success": True}

    def clipboard_read(self) -> str:
        if not self._user32.OpenClipboard(0):
            return ""
        try:
            # CF_UNICODETEXT = 13
            h_data = self._user32.GetClipboardData(13)
            if not h_data:
                return ""
            p_data = self._kernel32.GlobalLock(h_data)
            if not p_data:
                return ""
            try:
                return self._ctypes.wstring_at(p_data)
            finally:
                self._kernel32.GlobalUnlock(h_data)
        finally:
            self._user32.CloseClipboard()

    def clipboard_write(self, text: str) -> bool:
        if not self._user32.OpenClipboard(0):
            return False
        try:
            self._user32.EmptyClipboard()
            raw = (text + "\0").encode("utf-16le")
            # GMEM_MOVEABLE = 0x0002
            h_mem = self._kernel32.GlobalAlloc(0x0002, len(raw))
            if not h_mem:
                return False
            p_mem = self._kernel32.GlobalLock(h_mem)
            if not p_mem:
                return False
            try:
                self._ctypes.memmove(p_mem, raw, len(raw))
            finally:
                self._kernel32.GlobalUnlock(h_mem)
            return bool(self._user32.SetClipboardData(13, h_mem))
        finally:
            self._user32.CloseClipboard()


class MockComputerBackend(BaseComputerBackend):
    """Simulated in-memory backend for deterministic testing on any platform."""

    def __init__(
        self,
        display: DisplayInfo = DisplayInfo(1920, 1080),
        windows: list[WindowInfo] | None = None,
    ) -> None:
        self.display = display
        self.windows = list(windows) if windows is not None else [
            WindowInfo(
                handle=1001,
                title="Calculator",
                process_id=456,
                process_name="calc.exe",
                bounds=(100, 100, 500, 600),
                is_active=True,
                is_visible=True,
            ),
            WindowInfo(
                handle=1002,
                title="Notepad - Untitled",
                process_id=789,
                process_name="notepad.exe",
                bounds=(200, 200, 800, 700),
                is_active=False,
                is_visible=True,
            ),
        ]
        self.active_hwnd = self.windows[0].handle if self.windows else 0
        self.mouse_pos = (0, 0)
        self.clipboard_data = ""
        self.launched_processes: list[dict[str, Any]] = []
        self.type_history: list[str] = []
        self.hotkey_history: list[tuple[str, ...]] = []
        self.click_history: list[dict[str, Any]] = []

    def get_display_info(self) -> DisplayInfo:
        return self.display

    def screen_capture(self, region: ScreenRegion | None = None) -> dict[str, Any]:
        x = region.x if region else 0
        y = region.y if region else 0
        w = region.width if region else self.display.width
        h = region.height if region else self.display.height
        return {
            "format": "png_metadata",
            "region": {"x": x, "y": y, "width": w, "height": h},
            "captured": True,
            "screen_hash": "mock_screen_hash_12345",
        }

    def window_list(self, filter_title: str | None = None) -> list[WindowInfo]:
        if not filter_title:
            return list(self.windows)
        ft = filter_title.lower()
        return [w for w in self.windows if ft in w.title.lower()]

    def window_active(self) -> WindowInfo:
        for w in self.windows:
            if w.handle == self.active_hwnd:
                return w
        if self.windows:
            return self.windows[0]
        return WindowInfo(0, "None", is_active=False)

    def window_focus(self, handle: int | None = None, title: str | None = None) -> bool:
        target: WindowInfo | None = None
        if handle is not None:
            for w in self.windows:
                if w.handle == handle:
                    target = w
                    break
        elif title:
            for w in self.windows:
                if title.lower() in w.title.lower():
                    target = w
                    break
        if target is None:
            return False

        self.active_hwnd = target.handle
        updated_windows = []
        for w in self.windows:
            updated_windows.append(
                WindowInfo(
                    handle=w.handle,
                    title=w.title,
                    process_id=w.process_id,
                    process_name=w.process_name,
                    bounds=w.bounds,
                    is_active=(w.handle == target.handle),
                    is_visible=w.is_visible,
                )
            )
        self.windows = updated_windows
        return True

    def app_launch(self, app: str, argv: Sequence[str] = ()) -> dict[str, Any]:
        pid = 1000 + len(self.launched_processes) + 1
        record = {
            "pid": pid,
            "app": app,
            "argv": list(argv),
            "launched": True,
        }
        self.launched_processes.append(record)
        # Register a new window for the launched app
        new_w = WindowInfo(
            handle=2000 + pid,
            title=app.split("/")[-1].split("\\")[-1],
            process_id=pid,
            process_name=app,
            bounds=(50, 50, 600, 500),
            is_active=True,
            is_visible=True,
        )
        self.windows.append(new_w)
        self.active_hwnd = new_w.handle
        return record

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        self.mouse_pos = (x, y)
        return {"action": "move", "x": x, "y": y, "success": True}

    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self.mouse_pos = (x, y)
        rec = {"action": "click", "x": x, "y": y, "button": button, "clicks": clicks, "success": True}
        self.click_history.append(rec)
        return rec

    def keyboard_type(self, text: str) -> dict[str, Any]:
        self.type_history.append(text)
        return {"action": "type", "text": text, "length": len(text), "success": True}

    def keyboard_hotkey(self, keys: Sequence[str]) -> dict[str, Any]:
        self.hotkey_history.append(tuple(keys))
        return {"action": "hotkey", "keys": list(keys), "success": True}

    def clipboard_read(self) -> str:
        return self.clipboard_data

    def clipboard_write(self, text: str) -> bool:
        self.clipboard_data = text
        return True


__all__ = [
    "BaseComputerBackend",
    "MockComputerBackend",
    "UnsupportedPlatformBackend",
    "WindowsBackend",
]
