from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def append_decision(path: Path, action_id: str, decision: str) -> None:
    """Append a tamper-evident, metadata-only record of an approval decision."""
    record = {
        "action_id": action_id,
        "decision": decision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    previous_hash = ""
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous:
                previous_hash = str(previous[-1].get("hash", ""))
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
