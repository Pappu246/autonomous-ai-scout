from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


VALID_DECISIONS = {"approved", "rejected"}


def append_decision(path: Path, action_id: str, decision: str) -> None:
    """Append a tamper-evident, metadata-only record of an approval decision."""
    decision = decision.strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError("decision must be approved or rejected")
    record = {
        "action_id": action_id,
        "decision": decision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
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
    record["previous_hash"] = previous_hash
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record["hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
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
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append({str(k): str(v) for k, v in item.items()})
    return records


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
