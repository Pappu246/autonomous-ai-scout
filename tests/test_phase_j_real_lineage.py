from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.execution_engine import execute_plan
from autonomous_agent.task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan, TaskStep
from autonomous_agent.task_orchestrator import OrchestrationState, TaskOrchestrator


class _MemoryAdapter:
    def __init__(self, memory, project):
        self.memory = memory
        self.project = project

    def retrieve(self, project, task_digest):
        return self.memory.learn(project or self.project)

    def store_safe(self, evidence):
        self.memory.record_task(
            self.project,
            evidence["task_digest"],
            intent="orchestration",
            outcome=str(evidence.get("result", "observed")),
        )


class _RealPhaseGStepAdapter:
    """Test adapter around the real Phase-G execute_plan; it adds no execution logic."""

    def __init__(self, root, audit_path, memory, project, granted):
        self.root = Path(root)
        self.audit_path = Path(audit_path)
        self.memory = memory
        self.project = project
        self.granted = tuple(granted)
        self.calls = 0

    def execute_authorized(self, step_id, tool_name, task_digest):
        self.calls += 1
        from autonomous_agent.tool_registry import REGISTRY
        tool = REGISTRY.get(tool_name)
        assert tool is not None
        step = TaskStep(
            step_id=step_id,
            description="orchestrator integration step",
            tool_name=tool_name,
            risk=PlanRisk(tool.risk_level.value),
            authorization="authorized",
            execution_boundary="existing Phase-G executor",
            verification="verified by Phase-G executor",
        )
        plan_digest = task_digest
        plan = TaskPlan(
            task="inspect the project",
            intent=TaskIntent.INSPECT,
            steps=(step,),
            risk=PlanRisk(tool.risk_level.value),
            executable=True,
            reason="integration test",
            audit=TaskAuditRecord("inspect the project", TaskIntent.INSPECT, (step_id,), True, plan_digest),
        )
        result = execute_plan(
            plan,
            self.root,
            granted=self.granted,
            audit_path=self.audit_path,
            execution_id=f"integration-{self.calls}",
            memory=self.memory,
            project=self.project,
        )
        assert result.state.value == "verified", result.reason
        return result


def test_orchestrator_reaches_real_phase_g_and_phase_h(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    adapter = _RealPhaseGStepAdapter(
        tmp_path,
        tmp_path / "execution-audit.jsonl",
        memory,
        "project-a",
        (Capability.INSPECT, Capability.READ_FILE),
    )
    orchestrator = TaskOrchestrator(memory=_MemoryAdapter(memory, "project-a"))
    report = orchestrator.orchestrate("inspect the project", granted=(Capability.INSPECT, Capability.READ_FILE))
    assert report.state is OrchestrationState.AUTHORIZED

    completed = orchestrator.execute(report, adapter)
    assert completed.state is OrchestrationState.SUCCEEDED
    assert completed.verification is not None and completed.verification.success
    assert adapter.calls == len(report.steps)
    assert memory.learn("project-a")


def test_real_memory_never_grants_authority_across_projects(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    assert memory.record_task("project-a", "safe task", intent="execution", outcome="verified")
    evidence = tuple(memory.learn("project-a"))
    assert evidence
    assert tuple(memory.learn("project-b")) == ()

    orchestrator = TaskOrchestrator(memory=_MemoryAdapter(memory, "project-b"))
    report = orchestrator.orchestrate("inspect the project")
    assert report.state is OrchestrationState.BLOCKED


def test_real_memory_corruption_fails_closed(tmp_path):
    path = tmp_path / "memory.json"
    memory = CrossProjectMemory(path)
    assert memory.record_task("project-a", "safe task", intent="execution", outcome="verified")
    path.write_text("not-json", encoding="utf-8")
    assert tuple(memory.learn("project-a")) == ()
    assert memory.recommendation_needed("project-a", "safe recommendation") is True
