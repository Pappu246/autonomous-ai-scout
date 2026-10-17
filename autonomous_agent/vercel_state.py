"""Preview state namespace and a fail-closed distributed coordination boundary.

Vercel Blob is object storage, not a transaction/lock service. In the pinned
vercel==0.11.6 SDK, put(overwrite=False) sends x-allow-overwrite: 0, while
BlobClient.delete(path, token=...) sends an unconditional POST /delete. GET's
if_none_match is a read condition, not a conditional delete or fenced write.
A lease owner's GET followed by DELETE can therefore remove a successor's
lease; the same gap exists when reaping a stale lease. Do not use that protocol
for mission, approval, audit, queue, or memory state.

A local installation without Blob/Vercel environment markers uses the existing
single-host advisory file locks. A hosted installation (or any installation
configured with a Blob token) cannot safely assume a single writer and is
paused until an independently enforceable transactional/fencing coordinator is
implemented and tested. No environment switch can opt back into the unsafe
protocol. Existing Blob data is neither read, migrated, overwritten nor deleted.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


STATE_BLOB_PREFIX = "scout-state/files/"
LEGACY_PREVIEW_REF = "fix/rebased-mission-and-windows-20261012"
SHARED_STATE_PAUSED_REASON = (
    "atomic shared-state coordination is unavailable; hosted mission state is paused"
)


class SharedStateCoordinationUnavailable(RuntimeError):
    """Cloud state cannot be safely mutated with Blob's unfenced operations."""


def hosted_runtime() -> bool:
    return any(
        os.getenv(name, "").strip()
        for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL")
    )


def shared_state_coordination_required(*, token: str | None = None) -> bool:
    """Reject hosted or Blob-configured state, including local fake-Blob runs."""
    supplied = os.getenv("BLOB_READ_WRITE_TOKEN", "") if token is None else token
    return bool(hosted_runtime() or supplied.strip() or os.getenv("BLOB_READ_WRITE_TOKEN", "").strip())


def require_atomic_state_coordinator(*, token: str | None = None) -> None:
    if shared_state_coordination_required(token=token):
        raise SharedStateCoordinationUnavailable(SHARED_STATE_PAUSED_REASON)


def state_blob_key(path: str | Path) -> str | None:
    """Describe legacy/branch-isolated keys without reading or changing Blob."""
    target = Path(path)
    parts = target.parts
    state_indexes = [index for index, part in enumerate(parts) if part == "state"]
    if not state_indexes:
        return None
    relative = Path(*parts[state_indexes[-1] + 1 :]).as_posix()
    if relative in {"", "."} or relative.startswith("../"):
        return None
    scope = os.getenv("VERCEL_ENV", "local").strip().lower()
    if scope not in {"production", "preview", "development", "local"}:
        scope = "local"
    if scope == "preview":
        branch_ref = os.getenv("VERCEL_GIT_COMMIT_REF", "").strip()
        if not branch_ref:
            raise RuntimeError("preview shared state requires a Vercel branch ref")
        if branch_ref != LEGACY_PREVIEW_REF:
            digest = hashlib.sha256(branch_ref.encode("utf-8")).hexdigest()[:24]
            scope = f"preview/branches/{digest}"
    return STATE_BLOB_PREFIX + scope + "/" + relative


class VercelBlobStateSync:
    """Compatibility entry point that rejects the former unfenced lease path.

    Do not re-enable this by merely checking an owner string before deleting a
    Blob lease. The coordinator must support conditional/fenced state updates
    and a safe recovery protocol across *all* writers and deployments.
    """

    def __init__(
        self,
        target: str | Path,
        *,
        token: str | None = None,
        timeout_seconds: float = 20.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.target = Path(target)
        self.token = token or os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
        # Kept for callers constructing the old adapter; no SDK client or
        # legacy lease is created, regardless of these timeout hints.
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    @property
    def enabled(self) -> bool:
        return shared_state_coordination_required(token=self.token)

    def __enter__(self):
        require_atomic_state_coordinator(token=self.token)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


__all__ = [
    "SharedStateCoordinationUnavailable",
    "SHARED_STATE_PAUSED_REASON",
    "VercelBlobStateSync",
    "hosted_runtime",
    "require_atomic_state_coordinator",
    "shared_state_coordination_required",
    "state_blob_key",
]
