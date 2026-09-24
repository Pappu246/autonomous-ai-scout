import json
from pathlib import Path

from autonomous_agent.execution_engine import ExecutionResult, ExecutionState
from autonomous_agent.runtime import _plan_for_request, run_task
from autonomous_agent.capability_policy import Capability


def test_runtime_maps_safe_requests_to_bounded_plans():
    plan, grants = _plan_for_request("run the tests")
    assert plan.executable is True
    assert grants == (Capability.INSPECT, Capability.TEST)
    assert [step.tool_name for step in plan.steps] == ["github.inspect", "tests.run"]


def test_runtime_executes_safe_inspection_end_to_end(tmp_path: Path):
    result = run_task(
        "inspect repository",
        root=tmp_path,
        audit_path=tmp_path / "runtime.jsonl",
        execution_id="runtime-test",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results
    assert result.results[0].verification_status == "verified"


def test_runtime_fails_closed_for_unknown_request_only_after_safe_inspection():
    plan, grants = _plan_for_request("do something unspecified")
    assert plan.executable is True
    assert grants == (Capability.INSPECT,)


def test_runtime_derives_bounded_workspace_shell_request():
    from autonomous_agent.runtime import _workspace_request_for_task
    from autonomous_agent.task_core import AutonomousTaskCore

    task = "Use the canonical local workspace shell to run exactly this safe validation command: python -m py_compile autonomous_agent/runtime.py."
    prepared = AutonomousTaskCore().prepare(task)
    request = _workspace_request_for_task(task, prepared.plan)
    assert request == {"workspace.shell": {"argv": ("python", "-m", "py_compile", "autonomous_agent/runtime.py")}}


def test_runtime_executes_workspace_shell_end_to_end(tmp_path: Path):
    result = run_task(
        "Use the canonical local workspace shell to run exactly this command: pwd",
        root=tmp_path,
        audit_path=tmp_path / "shell-runtime.jsonl",
        journal_path=tmp_path / "shell-runtime-journal.jsonl",
        execution_id="workspace-shell-runtime",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results[-1].operation == "workspace_shell"
    payload = json.loads(result.results[-1].output)
    assert payload["argv"] == ["pwd"]
    assert payload["exit_status"] == 0
    assert payload["output"] == str(tmp_path.resolve())
    assert payload["success"] is True


def test_runtime_executes_filesystem_read_end_to_end(tmp_path: Path):
    (tmp_path / "README.md").write_text("runtime-read-ok", encoding="utf-8")
    result = run_task(
        "Read file README.md from the local workspace.",
        root=tmp_path,
        audit_path=tmp_path / "read-runtime.jsonl",
        journal_path=tmp_path / "read-runtime-journal.jsonl",
        execution_id="filesystem-read-runtime",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results[-1].operation == "filesystem_workspace"
    assert "runtime-read-ok" in result.results[-1].output


def test_runtime_derives_bounded_filesystem_read_request():
    from autonomous_agent.runtime import _workspace_request_for_task
    from autonomous_agent.task_core import AutonomousTaskCore

    task = "Read file README.md from the local workspace."
    prepared = AutonomousTaskCore().prepare(task)
    request = _workspace_request_for_task(task, prepared.plan)
    assert request == {"filesystem.read": {"operation": "read", "path": "README.md"}}


def test_runtime_wires_workspace_connector_for_workspace_task(monkeypatch, tmp_path: Path):
    from autonomous_agent.runtime import run_task
    captured = {}

    class FakeConnector:
        def __init__(self, root):
            captured["root"] = root

    class FakeCore:
        def __init__(self, *, registry):
            self.registry = registry

        def prepare(self, task, *, explicitly_approved=False):
            from autonomous_agent.task_core import AutonomousTaskCore
            return AutonomousTaskCore(registry=self.registry).prepare(
                task,
                explicitly_approved=explicitly_approved,
            )

        def execute(self, prepared, root, **kwargs):
            captured["explicitly_approved"] = kwargs.get("explicitly_approved")
            captured["connector"] = kwargs.get("workspace_connector")
            captured["request"] = kwargs.get("workspace_request")
            return ExecutionResult(ExecutionState.VERIFIED, "ok", 1, (), "audit")

    monkeypatch.setattr("autonomous_agent.runtime.WorkspaceConnector", FakeConnector)
    monkeypatch.setattr("autonomous_agent.runtime.AutonomousTaskCore", FakeCore)
    task = "Use the canonical local workspace shell to run exactly this safe validation command: python -m py_compile autonomous_agent/runtime.py."
    result = run_task(task, root=tmp_path, audit_path=tmp_path / "audit.jsonl", journal_path=tmp_path / "journal.jsonl", execution_id="workspace-runtime-test")
    assert result.state is ExecutionState.VERIFIED
    assert captured["root"] == tmp_path
    assert captured["explicitly_approved"] is False
    assert isinstance(captured["connector"], FakeConnector)
    assert captured["request"]["workspace.shell"]["argv"] == ("python", "-m", "py_compile", "autonomous_agent/runtime.py")


def test_runtime_parses_natural_language_file_creation_request():
    from autonomous_agent.runtime import _workspace_request_for_task
    from autonomous_agent.task_core import AutonomousTaskCore

    task = (
        "Create a harmless test file at state/scout_approval_test.txt containing: "
        "AUTONOMOUS_SCOUT_APPROVAL_TEST. Do not modify any other files."
    )
    prepared = AutonomousTaskCore().prepare(task, explicitly_approved=True)
    request = _workspace_request_for_task(task, prepared.plan)
    assert request == {
        "filesystem.write": {
            "operation": "write",
            "path": "state/scout_approval_test.txt",
            "content": "AUTONOMOUS_SCOUT_APPROVAL_TEST",
        }
    }


def test_runtime_parses_direct_readme_request():
    from autonomous_agent.runtime import _workspace_request_for_task
    from autonomous_agent.task_core import AutonomousTaskCore

    task = "Read README.md and give me a human-readable summary. Do not modify any files."
    prepared = AutonomousTaskCore().prepare(task)
    request = _workspace_request_for_task(task, prepared.plan)
    assert request == {"filesystem.read": {"operation": "read", "path": "README.md"}}


def test_runtime_reads_direct_readme_request_end_to_end(tmp_path: Path):
    (tmp_path / "README.md").write_text("Line one\nLine two\n│ section", encoding="utf-8")
    result = run_task(
        "Read README.md and give me a human-readable summary. Do not modify any files.",
        root=tmp_path,
        audit_path=tmp_path / "direct-read-runtime.jsonl",
        journal_path=tmp_path / "direct-read-journal.jsonl",
        execution_id="direct-read-runtime",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results[-1].success is True
    assert "READ VERIFIED: README.md" in result.results[-1].output
    assert "Line one\nLine two\n│ section" in result.results[-1].output



def test_runtime_builds_independent_requests_for_multiple_workspace_tools():
    from autonomous_agent.runtime import _workspace_request_for_task
    from autonomous_agent.task_plan_models import TaskPlan, TaskStep, TaskAuditRecord, TaskIntent, PlanRisk

    task = "Read file README.md and inspect the workspace. Do not modify any files."
    plan = TaskPlan(
        task=task,
        intent=TaskIntent.WORKSPACE,
        steps=(
            TaskStep("step-1", "List the workspace.", "filesystem.list", PlanRisk.LOW, "authorized", "", ""),
            TaskStep("step-2", "Read README.md.", "filesystem.read", PlanRisk.LOW, "authorized", "", ""),
        ),
        risk=PlanRisk.LOW,
        executable=True,
        reason="test",
        audit=TaskAuditRecord(task, TaskIntent.WORKSPACE, ("step-1", "step-2"), True, "test"),
    )
    request = _workspace_request_for_task(task, plan)
    assert request == {
        "filesystem.list": {"operation": "list", "path": "."},
        "filesystem.read": {"operation": "read", "path": "README.md"},
    }
