from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxPatch:
    id: str
    path: str
    diff: str
    test_command: str = "python -m pytest -q"
    applied: bool = False


def build_sandbox_patch(path: str, before: str, after: str) -> SandboxPatch:
    """Build a unified diff in memory only; this function never writes or applies files."""
    normalized = path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError("path must be a relative repository path")
    if before == after:
        raise ValueError("sandbox patch is empty")
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{normalized}",
            tofile=f"b/{normalized}",
        )
    )
    proposal_id = hashlib.sha256(f"{normalized}\0{diff}".encode("utf-8")).hexdigest()[:16]
    return SandboxPatch(id=proposal_id, path=normalized, diff=diff)
