"""Replay protection for state-mutating application adapter commands.

Prevents duplicate execution of state-mutating adapter operations on resume.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .models import ApplicationReplayError


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ApplicationReplayProtector:
    """Prevents duplicate execution of state-mutating application actions on resume."""

    def __init__(self, completed: Iterable[str] = ()) -> None:
        self._completed: set[str] = set(completed)

    def mutation_key(
        self,
        *,
        command: str,
        session_id: str,
        app_id: str,
        args: Sequence[str] = (),
        payload: str = "",
        target: Mapping[str, Any] | None = None,
    ) -> str:
        return _digest(
            {
                "command": command.strip().lower(),
                "session_id": session_id,
                "app_id": app_id.strip().lower(),
                "args": list(args),
                "payload": payload,
                "target": dict(target or {}),
            }
        )

    def already_completed(self, key: str) -> bool:
        return key in self._completed

    def check(self, key: str) -> None:
        """Raise if this mutation was already completed before resume."""
        if key in self._completed:
            raise ApplicationReplayError(
                "refusing to repeat an application mutation that already completed before resume"
            )

    def record(self, key: str) -> None:
        self._completed.add(key)

    def completed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    def reset(self) -> None:
        self._completed.clear()


__all__ = ["ApplicationReplayProtector"]
