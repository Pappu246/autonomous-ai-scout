from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RECORD_BYTES = 16_384
MAX_READ_RECORDS = 1_000

@dataclass(frozen=True)
class RunJournalRecord:
    execution_id: str
    task: str
    state: str
    reason: str
    attempts: int
    result_count: int
    recorded_at: str

def append_run_record(path: Path, record: RunJournalRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(payload.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("run journal record exceeds size limit")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")

def make_run_record(*, execution_id: str, task: str, result: Any) -> RunJournalRecord:
    return RunJournalRecord(
        execution_id=execution_id,
        task=" ".join(task.strip().split()),
        state=result.state.value,
        reason=str(result.reason),
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
