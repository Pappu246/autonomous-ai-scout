from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path


def _canonical(record: dict[str, str]) -> str:
    return json.dumps({k: v for k, v in record.items() if k != "hash"}, sort_keys=True, separators=(",", ":"))


def append_execution_record(path: Path, record: dict[str, str]) -> None:
    """Append one hash-chained execution record without storing raw credentials."""
    safe = {str(k): str(v) for k, v in record.items() if k != "hash"}
    previous_hash = ""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if line.strip():
                previous = json.loads(line)
                if not isinstance(previous, dict):
                    raise ValueError("execution audit contains an invalid record")
                previous_hash = str(previous.get("hash", ""))
                break
    safe["previous_hash"] = previous_hash
    safe["hash"] = hashlib.sha256(_canonical(safe).encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, sort_keys=True) + "\n")


def verify_execution_audit(path: Path) -> bool:
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
        except (TypeError, ValueError):
            return False
        if not isinstance(item, dict):
            return False
        record = {str(k): str(v) for k, v in item.items()}
        stored_hash = record.pop("hash", "")
        if not stored_hash or record.get("previous_hash", "") != previous_hash:
            return False
        expected = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(stored_hash, expected):
            return False
        previous_hash = stored_hash
    return True
