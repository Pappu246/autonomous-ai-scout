from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass(frozen=True)
class ExecutionLease:
    lease_id: str
    resource: str
    owner: str
    expires_at: float


class ConcurrencyError(ValueError):
    pass


class ExecutionLeaseStore:
    """Durable, bounded coordination primitive for existing workers/executors."""

    def __init__(self, path: str | Path, *, max_active: int = 4, ttl_seconds: int = 300):
        if max_active < 1 or max_active > 64:
            raise ValueError("max_active must be between 1 and 64")
        if ttl_seconds < 1 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        self.path = Path(path)
        self.max_active = max_active
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()

    def _load(self) -> list[ExecutionLease]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConcurrencyError("lease store is unreadable") from exc
        if not isinstance(payload, list):
            raise ConcurrencyError("lease store schema is invalid")
        now = time.time()
        leases: list[ExecutionLease] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ConcurrencyError("lease record is invalid")
            try:
                lease = ExecutionLease(
                    str(item["lease_id"]),
                    str(item["resource"]),
                    str(item["owner"]),
                    float(item["expires_at"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ConcurrencyError("lease record is malformed") from exc
            if lease.expires_at > now:
                leases.append(lease)
        return leases

    def _save(self, leases: list[ExecutionLease]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump([lease.__dict__ for lease in leases], handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def acquire(self, resource: str, owner: str) -> ExecutionLease | None:
        resource = str(resource).strip()
        owner = str(owner).strip()
        if not resource or not owner or len(resource) > 256 or len(owner) > 256:
            raise ConcurrencyError("resource and owner are required and bounded")
        with self._lock:
            leases = self._load()
            if any(item.resource == resource for item in leases):
                return None
            if len(leases) >= self.max_active:
                return None
            lease = ExecutionLease(uuid.uuid4().hex, resource, owner, time.time() + self.ttl_seconds)
            leases.append(lease)
            self._save(leases)
            return lease

    def release(self, lease_id: str) -> bool:
        with self._lock:
            leases = self._load()
            remaining = [lease for lease in leases if lease.lease_id != str(lease_id)]
            if len(remaining) == len(leases):
                return False
            self._save(remaining)
            return True

    def active(self) -> tuple[ExecutionLease, ...]:
        with self._lock:
            leases = self._load()
            self._save(leases)
            return tuple(leases)


__all__ = ["ConcurrencyError", "ExecutionLease", "ExecutionLeaseStore"]
