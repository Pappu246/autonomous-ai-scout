from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RECORD_BYTES = 16_384

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
