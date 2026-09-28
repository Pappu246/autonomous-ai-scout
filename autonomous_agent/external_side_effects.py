from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any


MAX_RECORDS = 10_000
MAX_TEXT = 4_000


class SideEffectState(str, Enum):
    RESERVED = "reserved"
    EXECUTED = "executed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SideEffectError(ValueError):
    pass


@dataclass(frozen=True)
class SideEffectRecord:
    key: str
    operation: str
    request_digest: str
    state: SideEffectState
    result_digest: str = ""
    reason: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class SideEffectDecision:
    allowed: bool
    replay_blocked: bool
    reason: str
    record: SideEffectRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_request_digest(operation: str, request: Any) -> str:
    if not isinstance(operation, str) or not operation.strip():
        raise SideEffectError("side-effect operation is required")
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): scrub(item)
                for key, item in sorted(value.items(), key=lambda item: str(item[0]))
                if str(key) not in {"approved", "__approved__"}
            }
        if isinstance(value, (list, tuple)):
            return [scrub(item) for item in value]
        if isinstance(value, bytes):
            return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "length": len(value)}
        return value
    encoded = json.dumps(
        {"operation": operation.strip(), "request": scrub(request)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ExternalSideEffectStore:
    """Durable fail-closed ledger for externally visible side effects.

    A RESERVED/UNKNOWN record is never automatically replayed because an
    interrupted remote call may already have changed the external system.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()

    def _validate(self, record: SideEffectRecord) -> None:
        if not record.key or len(record.key) > 512:
            raise SideEffectError("side-effect key is invalid")
        if not record.operation or len(record.operation) > 256:
            raise SideEffectError("side-effect operation is invalid")
        if len(record.request_digest) != 64:
            raise SideEffectError("side-effect request digest is invalid")
        if len(record.result_digest) > 64 or len(record.reason) > MAX_TEXT:
            raise SideEffectError("side-effect record is too large")
        for label, value in (("created_at", record.created_at), ("updated_at", record.updated_at)):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SideEffectError(f"{label} is invalid") from exc

    def _load_unlocked(self) -> dict[str, SideEffectRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SideEffectError("side-effect ledger is unreadable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise SideEffectError("side-effect ledger has an invalid schema")
        records: dict[str, SideEffectRecord] = {}
        for raw in payload["records"]:
            if not isinstance(raw, dict):
                raise SideEffectError("side-effect record is invalid")
            try:
                record = SideEffectRecord(
                    key=str(raw["key"]),
                    operation=str(raw["operation"]),
                    request_digest=str(raw["request_digest"]),
                    state=SideEffectState(str(raw["state"])),
                    result_digest=str(raw.get("result_digest", "")),
                    reason=str(raw.get("reason", "")),
                    created_at=str(raw["created_at"]),
                    updated_at=str(raw["updated_at"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SideEffectError("side-effect record is malformed") from exc
            self._validate(record)
            if record.key in records:
                raise SideEffectError("side-effect ledger contains duplicate keys")
            records[record.key] = record
            if len(records) > MAX_RECORDS:
                raise SideEffectError("side-effect ledger exceeds its record limit")
        return records

    def _save_unlocked(self, records: dict[str, SideEffectRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [
                {
                    "created_at": record.created_at,
                    "key": record.key,
                    "operation": record.operation,
                    "reason": record.reason,
                    "request_digest": record.request_digest,
                    "result_digest": record.result_digest,
                    "state": record.state.value,
                    "updated_at": record.updated_at,
                }
                for record in sorted(records.values(), key=lambda item: item.key)
            ]
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def get(self, key: str) -> SideEffectRecord | None:
        with self._lock:
            return self._load_unlocked().get(key)

    def claim(self, *, key: str, operation: str, request_digest: str) -> SideEffectDecision:
        now = _now()
        candidate = SideEffectRecord(
            key=str(key).strip(),
            operation=str(operation).strip(),
            request_digest=str(request_digest).strip(),
            state=SideEffectState.RESERVED,
            created_at=now,
            updated_at=now,
        )
        self._validate(candidate)
        with self._lock:
            records = self._load_unlocked()
            existing = records.get(candidate.key)
            if existing is None:
                records[candidate.key] = candidate
                self._save_unlocked(records)
                return SideEffectDecision(True, False, "side effect reserved for one external execution", candidate)
            if existing.operation != candidate.operation or existing.request_digest != candidate.request_digest:
                raise SideEffectError("side-effect key was reused for a different request")
            if existing.state is SideEffectState.EXECUTED:
                return SideEffectDecision(False, True, "side effect was already executed; it has already been executed; automatic replay is blocked", existing)
            if existing.state in {SideEffectState.RESERVED, SideEffectState.UNKNOWN}:
                return SideEffectDecision(False, True, "side effect has an unresolved prior execution; reconciliation is required before replay", existing)
            return SideEffectDecision(False, True, "side effect has a terminal failed record; automatic replay is blocked", existing)

    def _update(
        self,
        key: str,
        *,
        state: SideEffectState,
        result_digest: str = "",
        reason: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            records = self._load_unlocked()
            current = records.get(key)
            if current is None:
                raise SideEffectError("side-effect record does not exist")
            if current.state is not SideEffectState.RESERVED:
                raise SideEffectError("side-effect record is no longer claimable")
            updated = SideEffectRecord(
                key=current.key,
                operation=current.operation,
                request_digest=current.request_digest,
                state=state,
                result_digest=result_digest[:64],
                reason=str(reason)[:MAX_TEXT],
                created_at=current.created_at,
                updated_at=_now(),
            )
            self._validate(updated)
            records[key] = updated
            self._save_unlocked(records)
            return updated

    def mark_executed(self, key: str, *, result_digest: str = "") -> SideEffectRecord:
        return self._update(key, state=SideEffectState.EXECUTED, result_digest=result_digest)

    def mark_failed(self, key: str, *, reason: str) -> SideEffectRecord:
        return self._update(key, state=SideEffectState.FAILED, reason=reason)

    def mark_unknown(self, key: str, *, reason: str) -> SideEffectRecord:
        return self._update(key, state=SideEffectState.UNKNOWN, reason=reason)


__all__ = [
    "ExternalSideEffectStore",
    "SideEffectDecision",
    "SideEffectError",
    "SideEffectRecord",
    "SideEffectState",
    "canonical_request_digest",
]
