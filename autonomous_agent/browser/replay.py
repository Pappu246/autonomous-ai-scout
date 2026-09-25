"""Replay protection for state-mutating browser actions.

When a bounded browser workflow is checkpointed and later resumed, a naive
resume would re-run already-completed mutating steps (a click, a form submit, a
download). This protector records a digest of every completed mutation and
refuses to repeat one, so resume can only continue with genuinely new work.

Idempotent, read-only operations (navigate, back, forward, reload, observe,
find) are never gated here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BrowserReplayProtector:
    """Prevents duplicate execution of state-mutating browser actions on resume."""

    def __init__(self, completed: Iterable[str] = ()) -> None:
        self._completed: set[str] = set(completed)

    def mutation_key(
        self,
        *,
        operation: str,
        session_id: str,
        url: str,
        target: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        return _digest(
            {
                "operation": operation,
                "session_id": session_id,
                "url": url,
                "target": dict(target or {}),
                "payload": dict(payload or {}),
            }
        )

    def already_completed(self, key: str) -> bool:
        return key in self._completed

    def check(self, key: str) -> None:
        """Raise if this mutation was already completed (resume replay)."""
        from .models import BrowserReplayError

        if key in self._completed:
            raise BrowserReplayError(
                "refusing to repeat a mutation that already completed before resume"
            )

    def record(self, key: str) -> None:
        self._completed.add(key)

    def completed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    def reset(self) -> None:
        self._completed.clear()


__all__ = ["BrowserReplayProtector"]
