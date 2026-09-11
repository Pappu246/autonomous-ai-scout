import json
from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.cross_project_memory import CrossProjectMemory, MemoryEvent
from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.project_intelligence import analyze_project
from autonomous_agent.task_planner import plan_task


def _inspect_plan():
    plan = plan_task("inspect repository", granted=[Capability.INSPECT, Capability.READ_FILE])
    assert plan.executable
    return type(plan)(plan.task, plan.intent, (plan.steps[0],), plan.risk, True, plan.reason, plan.audit)


def test_memory_tampering_fails_closed_for_reads_and_writes(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = CrossProjectMemory(path)
    assert memory.record_task("owner/repo", "inspect", intent="inspect", outcome="success")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[0]["outcome"] = "poisoned"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert memory.learn("owner/repo") == ()
    assert not memory.record_task("owner/repo", "new", intent="inspect", outcome="success")


def test_memory_cannot_elevate_capability_or_authorize_execution(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    memory.record(MemoryEvent("owner/repo", "authorization", "grant-source-write", "accepted", {"capability": "source_write"}))
    plan = plan_task("fix the bug", granted=[])
    assert not plan.executable
    result = execute_plan(plan, tmp_path, granted=[], audit_path=tmp_path / "audit.jsonl", execution_id="elevate", memory=memory, project="owner/repo")
    assert result.state is ExecutionState.BLOCKED


def test_project_memory_isolated_and_stale_findings_do_not_cross_projects(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    assert memory.record_finding("owner/repo-a", "missing license", severity="low")
    assert memory.recommendation_needed("owner/repo-b", "add a license")
    assert len(memory.learn("owner/repo-a", kind="finding")) == 1
    assert memory.learn("owner/repo-b", kind="finding") == ()


def test_failed_memory_write_is_observational_and_does_not_change_execution_policy(tmp_path: Path):
    class FailingMemory(CrossProjectMemory):
        def _save(self, entries):
            return False

    memory = FailingMemory(tmp_path / "memory.json")
    assert not memory.record_task("owner/repo", "inspect", intent="execution", outcome="started")
    plan = _inspect_plan()
    audit = tmp_path / "audit.jsonl"
    result = execute_plan(plan, tmp_path, granted=[Capability.INSPECT], audit_path=audit, execution_id="write-failure", memory=memory, project="owner/repo")
    assert result.state is ExecutionState.VERIFIED


def test_restart_recovery_does_not_replay_from_memory(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    memory.record_task("owner/repo", "inspect", intent="execution", outcome="started")
    plan = _inspect_plan()
    audit = tmp_path / "audit.jsonl"
    from autonomous_agent.execution_audit import append_execution_record
    append_execution_record(audit, {"execution_id": "restart", "state": "running", "timestamp": "now"})
    result = execute_plan(plan, tmp_path, granted=[Capability.INSPECT], audit_path=audit, execution_id="restart", memory=memory, project="owner/repo")
    assert result.state is ExecutionState.RECOVERY_REQUIRED


def test_project_intelligence_persists_only_observational_evidence(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1"}}', encoding="utf-8")
    memory = CrossProjectMemory(tmp_path / "memory.json")
    findings = analyze_project(tmp_path, "owner/repo", memory=memory)
    assert findings
    entries = memory.learn("owner/repo")
    kinds = {entry["kind"] for entry in entries}
    assert "finding" in kinds
    assert "recommendation" in kinds
    assert "health_baseline" in kinds
    text = (tmp_path / "memory.json").read_text(encoding="utf-8")
    assert "authorize" not in text.lower()
    assert "capability" not in text.lower()


def test_bounded_storage_survives_restarts_deterministically(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = CrossProjectMemory(path, max_entries=6, max_entries_per_project=2)
    for index in range(20):
        memory.record(MemoryEvent("owner/repo", "task", str(index), "success", {"index": index}))
    restarted = CrossProjectMemory(path, max_entries=6, max_entries_per_project=2)
    assert len(restarted.learn("owner/repo")) == 2
    assert restarted.learn("owner/repo")[0]["fingerprint"] == memory.learn("owner/repo")[0]["fingerprint"]


def test_fingerprints_are_deterministic_across_instances(tmp_path: Path):
    first = CrossProjectMemory(tmp_path / "a.json")
    second = CrossProjectMemory(tmp_path / "b.json")
    first.record_finding("owner/repo", "missing license", severity="low")
    second.record_finding("owner/repo", "missing license", severity="low")
    assert first.learn("owner/repo", kind="finding")[0]["fingerprint"] == second.learn("owner/repo", kind="finding")[0]["fingerprint"]
