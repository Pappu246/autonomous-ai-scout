from pathlib import Path

import pytest

from autonomous_agent.approval_audit import append_decision, verify_audit_chain


def test_approval_audit_refuses_to_extend_tampered_chain(tmp_path: Path):
    path = tmp_path / "approval.jsonl"
    append_decision(path, "one", "approved")
    raw = path.read_text(encoding="utf-8").replace('"approved"', '"rejected"', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        append_decision(path, "two", "approved")
    assert not verify_audit_chain(path)


def _append_approval_records(path_str: str, worker: int) -> None:
    from pathlib import Path
    from autonomous_agent.approval_audit import append_decision
    path = Path(path_str)
    for index in range(8):
        append_decision(path, f"{worker}-{index}", "approved")


def test_approval_audit_chain_survives_concurrent_processes(tmp_path: Path):
    from multiprocessing import Process
    path = tmp_path / "approval.jsonl"
    workers = [Process(target=_append_approval_records, args=(str(path), worker)) for worker in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 32
    assert verify_audit_chain(path)
