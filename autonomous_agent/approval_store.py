from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .action_queue import PendingAction, load_queue
from .approved_executor import ApprovalRecord
from .approval import set_decision


def _safe_action_id(action_id: str) -> str:
    value = action_id.strip()
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in value):
        raise ValueError("invalid action id")
    return value


def _approval_path(directory: Path, action_id: str) -> Path:
    return directory / f"{_safe_action_id(action_id)}.json"


def _write_private(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        temporary = None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise OSError("approval record could not be persisted atomically") from exc
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def create_approval(
    queue_path: Path,
    approval_dir: Path,
    action_id: str,
    *,
    audit_path: Path | None = None,
    approved_at: datetime | None = None,
    ttl: timedelta | None = None,
) -> ApprovalRecord:
    safe_id = _safe_action_id(action_id)
    queue = load_queue(queue_path)
    action = next((item for item in queue if item.id == safe_id), None)
    if action is None:
        raise KeyError(f"approval action not found: {action_id}")
    token = secrets.token_urlsafe(32)
    record = ApprovalRecord.for_action(
        action,
        token,
        approved_at=approved_at,
        ttl=ttl or timedelta(hours=24),
    )
    approval_path = _approval_path(approval_dir, action_id)
    _write_private(approval_path, asdict(record))
    try:
        set_decision(queue_path, action_id, "approved", audit_path)
    except Exception:
        try:
            approval_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return record


def reject_action(
    queue_path: Path,
    action_id: str,
    *,
    audit_path: Path | None = None,
) -> PendingAction:
    return set_decision(queue_path, action_id, "rejected", audit_path)


def load_approval(approval_dir: Path, action_id: str) -> ApprovalRecord:
    path = _approval_path(approval_dir, action_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRecord(
            str(data["action_id"]),
            str(data["approved_at"]),
            str(data["expires_at"]),
            str(data["approval_token"]),
            str(data.get("action_digest", "")),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("approval record is missing or invalid") from exc
