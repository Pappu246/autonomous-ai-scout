from pathlib import Path

import pytest

from autonomous_agent.execution_audit import append_execution_record, verify_execution_audit


def test_execution_audit_refuses_to_extend_tampered_chain(tmp_path: Path):
    path = tmp_path / "execution.jsonl"
    append_execution_record(path, {"execution_id": "one", "state": "running"})
    raw = path.read_text(encoding="utf-8").replace('"running"', '"tampered"', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        append_execution_record(path, {"execution_id": "two", "state": "running"})
    assert not verify_execution_audit(path)


def _append_audit_records(path_str: str, worker: int) -> None:
    from pathlib import Path
    from autonomous_agent.execution_audit import append_execution_record
    path = Path(path_str)
    for index in range(8):
        append_execution_record(
            path,
            {"execution_id": f"{worker}-{index}", "state": "running"},
        )


def test_execution_audit_chain_survives_concurrent_processes(tmp_path):
    from multiprocessing import Process
    path = tmp_path / "execution.jsonl"
    workers = [Process(target=_append_audit_records, args=(str(path), worker)) for worker in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 32
    assert verify_execution_audit(path)
