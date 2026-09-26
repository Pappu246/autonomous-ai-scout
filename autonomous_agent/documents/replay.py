"""Replay protection for state-mutating document transformations.

Prevents re-running already completed mutating document operations (conversions,
merges, splits) during resume.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .models import DocumentReplayError


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DocumentsReplayProtector:
    """Prevents duplicate execution of state-mutating document actions on resume."""

    def __init__(self, completed: Iterable[str] = ()) -> None:
        self._completed: set[str] = set(completed)

    def mutation_key(
        self,
        *,
        operation: str,
        session_id: str,
        input_paths: Sequence[str],
        output_path: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> str:
        return _digest(
            {
                "operation": operation,
                "session_id": session_id,
                "input_paths": sorted(input_paths),
                "output_path": output_path,
                "parameters": dict(parameters or {}),
            }
        )

    def already_completed(self, key: str) -> bool:
        return key in self._completed

    def check(self, key: str) -> None:
        """Raise if this mutation was already completed before resume."""
        if key in self._completed:
            raise DocumentReplayError(
                "refusing to repeat a document mutation that already completed before resume"
            )

    def record(self, key: str) -> None:
        self._completed.add(key)

    def completed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    def reset(self) -> None:
        self._completed.clear()


__all__ = ["DocumentsReplayProtector"]
