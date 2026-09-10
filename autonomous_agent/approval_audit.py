from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path


VALID_DECISIONS = {"approved", "rejected"}


def _canonical_payload(record: dict[str, str]) -> str:
    return json.dumps(
        {key: value for key, value in record.items() if key != "hash"},
        sort_keys=True,
        separators=(",", ":"),
    )


def append_decision(path: Path, action_id: str, decision: str) -> None:
    """Append a tamper-evident, metadata-only record of an approval decision."""
    decision = decision.strip().lower()
    action_id = action_id.strip()
    if decision not in VALID_DECISIONS:
        raise ValueError("decision must be approved or rejected")
    if not action_id:
        raise ValueError("action_id must not be empty")
    previous_hash = ""
    if path.exists():
        try:
            for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                previous = json.loads(line)
                if isinstance(previous, dict):
                    previous_hash = str(previous.get("hash", ""))
                    break
        except (OSError, ValueError):
            previous_hash = ""
    record: dict[str, str] = {
        "action_id": action_id,
        "decision": decision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "previous_hash": previous_hash,
    }
    record["hash"] = hashlib.sha256(_canonical_payload(record).encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_audit(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    records: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict):
            records.append({str(k): str(v) for k, v in item.items()})
    return records


def verify_audit_chain(path: Path) -> bool:
    """Verify every audit record hash and its link to the previous record."""
    if not path.exists():
        return True
    previous_hash = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            return False
        if not isinstance(item, dict):
            return False
        record = {str(k): str(v) for k, v in item.items()}
        stored_hash = record.pop("hash", "")
        if not stored_hash or record.get("previous_hash", "") != previous_hash:
            return False
        expected_hash = hashlib.sha256(_canonical_payload(record).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(stored_hash, expected_hash):
            return False
        previous_hash = stored_hash
    return True


def load_audit_log(path: Path) -> list[dict[str, str]]:
    """Compatibility view exposing only non-sensitive audit metadata."""
    return [
        {
            "action_id": entry["action_id"],
            "decision": entry["decision"],
            "recorded_at": entry.get("timestamp", entry.get("recorded_at", "")),
        }
        for entry in read_audit(path)
        if {"action_id", "decision"} <= entry.keys()
    ]
