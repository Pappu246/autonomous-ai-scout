"""Replay protection for state-mutating computer control actions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .models import WindowInfo


class ComputerReplayProtector:
    """Prevents duplicate execution of state-mutating computer actions.

    Specifically prevents:
    - app.launch spawning a second instance on resume or retry
    - keyboard.type re-entering text into a field on resume or retry
    """

    def __init__(self) -> None:
        self._completed_launches: dict[str, dict[str, Any]] = {}
        self._completed_types: dict[str, dict[str, Any]] = {}
        self._completed_writes: dict[str, dict[str, Any]] = {}

    def get_existing_launch(
        self,
        app: str,
        argv: tuple[str, ...],
        idempotency_key: str | None,
        existing_windows: list[WindowInfo],
    ) -> dict[str, Any] | None:
        """Check if application was already launched or is already running."""
        # 1. Check idempotency key if provided
        if idempotency_key and idempotency_key in self._completed_launches:
            cached = dict(self._completed_launches[idempotency_key])
            cached["cached"] = True
            return cached

        # 2. Check canonical launch key (app + argv)
        key = self._launch_key(app, argv)
        if key in self._completed_launches:
            cached = dict(self._completed_launches[key])
            cached["cached"] = True
            return cached

        # 3. Check if window with matching title or process name is already open
        app_base = app.lower().replace("\\", "/").split("/")[-1].replace(".exe", "")
        for w in existing_windows:
            w_title = w.title.lower()
            w_proc = w.process_name.lower()
            if (app_base in w_proc) or (app_base in w_title):
                rec = {
                    "pid": w.process_id,
                    "app": app,
                    "argv": list(argv),
                    "launched": False,
                    "reused_existing": True,
                    "handle": w.handle,
                    "window_title": w.title,
                }
                return rec

        return None

    def record_launch(
        self,
        app: str,
        argv: tuple[str, ...],
        idempotency_key: str | None,
        result: dict[str, Any],
    ) -> None:
        """Record completed app launch for replay detection."""
        key = self._launch_key(app, argv)
        self._completed_launches[key] = result
        if idempotency_key:
            self._completed_launches[idempotency_key] = result

    def get_existing_type(
        self,
        text: str,
        idempotency_key: str | None,
        target_handle: int | None = None,
    ) -> dict[str, Any] | None:
        """Check if typing action was already executed."""
        if idempotency_key and idempotency_key in self._completed_types:
            cached = dict(self._completed_types[idempotency_key])
            cached["cached"] = True
            return cached

        key = self._type_key(text, target_handle)
        if key in self._completed_types:
            cached = dict(self._completed_types[key])
            cached["cached"] = True
            return cached

        return None

    def record_type(
        self,
        text: str,
        idempotency_key: str | None,
        target_handle: int | None,
        result: dict[str, Any],
    ) -> None:
        """Record completed typing action for replay detection."""
        key = self._type_key(text, target_handle)
        self._completed_types[key] = result
        if idempotency_key:
            self._completed_types[idempotency_key] = result

    def _launch_key(self, app: str, argv: tuple[str, ...]) -> str:
        payload = json.dumps({"app": app.lower(), "argv": list(argv)}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _type_key(self, text: str, target_handle: int | None) -> str:
        payload = json.dumps({"text": text, "handle": target_handle}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def reset(self) -> None:
        self._completed_launches.clear()
        self._completed_types.clear()
        self._completed_writes.clear()


__all__ = ["ComputerReplayProtector"]
