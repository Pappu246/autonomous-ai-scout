from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from autonomous_agent import runtime
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionResult, ExecutionState
from autonomous_agent.task_core import AutonomousTaskCore
from autonomous_agent.task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan
from autonomous_agent.task_orchestrator import StructuredTask


def test_prepare_normalizes_and_derives_safe_capability_grants() -> None:
    core = AutonomousTaskCore()

    prepared = core.prepare("   Inspect   repository   ")

    assert prepared.task == "Inspect repository"
    assert prepared.project is None
    assert len(prepared.task_digest) == 64
    assert prepared.granted == (Capability.INSPECT,)
    assert prepared.plan.intent is TaskIntent.INSPECT
    assert prepared.plan.executable is True
    assert prepared.plan.steps[0].tool_name == "github.inspect"


def test_prepare_is_deterministic_for_same_request() -> None:
    core = AutonomousTaskCore()

    first = core.prepare("research the project")
    second = core.prepare("research the project")

    assert first.task_digest == second.task_digest
    assert first.plan.audit.plan_digest == second.plan.audit.plan_digest
    assert first.plan.steps == second.plan.steps
    assert first.granted == second.granted == (Capability.WEB_RESEARCH,)


def test_explicit_approval_grants_safe_workspace_write_capability() -> None:
    core = AutonomousTaskCore()

    blocked = core.prepare("transform file config.py")
    assert blocked.plan.executable is False
    assert Capability.FILES_WORKSPACE not in blocked.granted

    approved = core.prepare("transform file config.py", explicitly_approved=True)
    assert approved.plan.executable is True
    assert Capability.FILES_WORKSPACE in approved.granted


def test_change_request_remains_blocked_by_existing_approval_boundary() -> None:
    core = AutonomousTaskCore()

    prepared = core.prepare("fix the failing tests")

    assert prepared.plan.executable is False
    assert prepared.plan.risk in {PlanRisk.HIGH, PlanRisk.CRITICAL}
    assert any(step.tool_name == "github.change" for step in prepared.plan.steps)
    assert Capability.SOURCE_WRITE not in prepared.granted


def test_prepare_accepts_explicit_capability_grants_without_widening_them() -> None:
    core = AutonomousTaskCore()

    prepared = core.prepare(
        StructuredTask("inspect repository", project="owner/repo"),
        granted=(Capability.INSPECT, Capability.INSPECT),
    )

    assert prepared.granted == (Capability.INSPECT,)
    assert prepared.project == "owner/repo"
    assert prepared.plan.executable is True


def test_execute_delegates_to_existing_execution_boundary(monkeypatch, tmp_path: Path) -> None:
    core = AutonomousTaskCore()
    prepared = core.prepare("inspect repository")
    captured: dict[str, object] = {}
    sentinel = ExecutionResult(ExecutionState.VERIFIED, "sentinel", 1, (), "audit")

    def fake_execute(plan, root, **kwargs):
        captured["plan"] = plan
        captured["root"] = root
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("autonomous_agent.task_core.execute_plan", fake_execute)

    result = core.execute(
        prepared,
        tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="exec-15",
    )

    assert result is sentinel
    assert captured["plan"] is prepared.plan
    assert captured["root"] == tmp_path
    assert captured["granted"] == prepared.granted
    assert captured["execution_id"] == "exec-15"


def test_runtime_uses_core_as_its_single_planning_entrypoint(monkeypatch, tmp_path: Path) -> None:
    plan = TaskPlan(
        task="unsupported",
        intent=TaskIntent.UNKNOWN,
        steps=(),
        risk=PlanRisk.LOW,
        executable=False,
        reason="blocked by test double",
        audit=TaskAuditRecord("unsupported", TaskIntent.UNKNOWN, (), False, "digest"),
    )
    prepared = SimpleNamespace(plan=plan, granted=())
    calls: list[str] = []

    class FakeCore:
        def __init__(self, *, registry):
            calls.append("init")

        def prepare(self, task):
            calls.append(f"prepare:{task}")
            return prepared

    monkeypatch.setattr(runtime, "AutonomousTaskCore", FakeCore)

    result = runtime.run_task(
        "unsupported",
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        journal_path=tmp_path / "journal.jsonl",
        execution_id="runtime-15",
    )

    assert result.state is ExecutionState.BLOCKED
    assert calls == ["init", "prepare:unsupported"]
