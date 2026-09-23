from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RECORD_BYTES = 16_384
MAX_READ_RECORDS = 1_000
MAX_JOURNAL_BYTES = 1_048_576

_SECRET = re.compile(r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----", re.S)

def _safe_text(value: object, limit: int) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED]", str(value))
    text = _SECRET.sub("[REDACTED]", text)
    return " ".join(text.strip().split())[:limit]


@dataclass(frozen=True)
class RunJournalRecord:
    execution_id: str
    task: str
    state: str
    reason: str
    attempts: int
    result_count: int
    recorded_at: str

def _compact_for_append(path: Path, incoming_bytes: int) -> None:
    """Keep the newest complete lines that fit before appending a record."""
    if incoming_bytes > MAX_JOURNAL_BYTES:
        raise ValueError("run journal record exceeds journal size limit")
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        return
    if len(existing) + incoming_bytes <= MAX_JOURNAL_BYTES:
        return

    budget = MAX_JOURNAL_BYTES - incoming_bytes
    kept: list[bytes] = []
    total = 0
    for raw in reversed(existing.splitlines(keepends=True)):
        if total + len(raw) > budget:
            break
        kept.append(raw)
        total += len(raw)
    kept.reverse()
    path.write_bytes(b"".join(kept))

def append_run_record(path: Path, record: RunJournalRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > MAX_RECORD_BYTES:
        raise ValueError("run journal record exceeds size limit")
    line = payload_bytes + b"\n"
    _compact_for_append(path, len(line))
    with path.open("ab") as handle:
        handle.write(line)

def make_run_record(*, execution_id: str, task: str, result: Any) -> RunJournalRecord:
    return RunJournalRecord(
        execution_id=execution_id,
        task=_safe_text(task, 4000),
        state=_safe_text(result.state.value, 128),
        reason=_safe_text(result.reason, 4096),
        attempts=int(result.attempts),
        result_count=len(result.results),
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )

def read_run_records(path: Path, *, limit: int = 100) -> tuple[RunJournalRecord, ...]:
    """Read up to ``limit`` valid records without granting execution authority."""
    if limit < 1 or limit > MAX_READ_RECORDS:
        raise ValueError(f"journal read limit must be between 1 and {MAX_READ_RECORDS}")
    if not path.exists():
        return ()

    records: list[RunJournalRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if len(records) >= limit:
                break
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                records.append(
                    RunJournalRecord(
                        execution_id=str(payload["execution_id"]),
                        task=str(payload["task"]),
                        state=str(payload["state"]),
                        reason=str(payload["reason"]),
                        attempts=int(payload["attempts"]),
                        result_count=int(payload["result_count"]),
                        recorded_at=str(payload["recorded_at"]),
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    return tuple(records)

def summarize_run_records(records: tuple[RunJournalRecord, ...]) -> dict[str, int]:
    """Return bounded state counts for reporting and observability."""
    summary = {"total": len(records), "verified": 0, "blocked": 0, "failed": 0}
    for record in records:
        if record.state in summary:
            summary[record.state] += 1
    return summary
