"""Phase 10 — real end-to-end canary coverage for the canonical agent lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from autonomous_agent.digital import DigitalResultState, build_agent
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.filesystem_workspace import WorkspaceConnector


def _agent(root: Path):
    return build_agent(root=root, connectors={"workspace": WorkspaceConnector(root)})


def test_e2e_canary_read_verify_and_audit(tmp_path: Path):
    (tmp_path / "README.md").write_text("canary-line-1\ncanary-line-2\n", encoding="utf-8")
    audit = tmp_path / "state" / "canary-read.jsonl"

    result = _agent(tmp_path).run(
        "Read README.md and give me a human-readable summary. Do not modify any files.",
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-read-1",
    )

    assert result.state is DigitalResultState.VERIFIED
    assert result.verified
    assert result.steps
    assert result.steps[0].verified is True
    assert result.steps[0].observation == "workspace read evidence"
    assert verify_execution_audit(audit)

    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    audit_text = audit.read_text(encoding="utf-8")
    assert "canary-line-1" in audit_text
    assert any(item.get("event") == "capability_result" and item.get("verification") == "verified" for item in events)
    assert any(item.get("event") == "run_verified" and item.get("state") == DigitalResultState.VERIFIED.value for item in events)


def test_e2e_canary_side_effect_requires_approval(tmp_path: Path):
    audit = tmp_path / "state" / "canary-write.jsonl"

    blocked = _agent(tmp_path).run(
        "Create file canary.txt containing safe-canary-content.",
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-write-blocked",
    )

    assert blocked.state is DigitalResultState.REQUIRES_APPROVAL
    assert not (tmp_path / "canary.txt").exists()

    approved = _agent(tmp_path).run(
        "Create file canary.txt containing safe-canary-content.",
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-write-approved",
        explicitly_approved=True,
        granted=["files_workspace"],
    )

    assert approved.state is DigitalResultState.VERIFIED
    assert (tmp_path / "canary.txt").read_text(encoding="utf-8") == "safe-canary-content"


def test_e2e_canary_resume_skips_verified_work(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "existing.txt").write_text("existing", encoding="utf-8")

    audit = tmp_path / "state" / "canary-resume.jsonl"
    checkpoint = tmp_path / "state" / "canary-resume-checkpoint.json"
    goal = "List the files in the docs folder and read the file notes.txt."
    requests = {
        "filesystem:list": {"path": "docs"},
        "filesystem:read": {"path": "docs/notes.txt"},
    }

    first = _agent(tmp_path).run(
        goal,
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-resume-1",
        checkpoint_path=checkpoint,
        requests=requests,
    )
    assert first.state is DigitalResultState.FAILED
    assert first.steps[0].verified is True
    assert first.steps[1].verified is False
    assert checkpoint.exists()

    (docs / "notes.txt").write_text("recovered", encoding="utf-8")

    second = _agent(tmp_path).run(
        goal,
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-resume-1",
        checkpoint_path=checkpoint,
        resume=True,
        requests=requests,
    )

    assert second.state is DigitalResultState.VERIFIED
    assert second.resumed_steps == ("step-1",)
    assert second.steps[0].resumed is True
    assert second.steps[0].verified is True
    assert second.steps[1].verified is True
    assert second.steps[1].observation == "workspace read evidence"
    assert "recovered" in audit.read_text(encoding="utf-8")
    assert verify_execution_audit(audit)


def test_e2e_canary_reserved_capability_fails_closed(tmp_path: Path):
    audit = tmp_path / "state" / "canary-reserved.jsonl"

    result = _agent(tmp_path).run(
        "Extract the text from the PDF report.",
        root=tmp_path,
        audit_path=audit,
        execution_id="canary-reserved-1",
    )

    assert result.state is DigitalResultState.BLOCKED
    assert "not yet registered" in result.reason.lower()
    assert verify_execution_audit(audit)
