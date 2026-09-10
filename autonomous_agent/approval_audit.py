from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


VALID_DECISIONS = {"approved", "rejected"}


def append_decision(path: Path, action_id: str, decision: str) -> None:
    """Append an approval decision without storing secrets or executing the action."""
    if decision not in VALID_DECISIONS:
        raise ValueError("decision must be approved or rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "action_id": action_id,
        "decision": decision,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def load_audit_log(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    entries: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and {"action_id", "decision", "recorded_at"} <= item.keys():
            entries.append({key: str(item[key]) for key in ("action_id", "decision", "recorded_at")})
    return entries
