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


def test_runtime_wires_workspace_connector_for_workspace_task(monkeypatch, tmp_path: Path):
    from autonomous_agent.runtime import run_task
    captured = {}

    class FakeConnector:
        def __init__(self, root):
            captured["root"] = root

    class FakeCore:
        def __init__(self, *, registry):
            self.registry = registry

        def prepare(self, task):
            from autonomous_agent.task_core import AutonomousTaskCore
            return AutonomousTaskCore(registry=self.registry).prepare(task)

        def execute(self, prepared, root, **kwargs):
            captured["connector"] = kwargs.get("workspace_connector")
            captured["request"] = kwargs.get("workspace_request")
            return ExecutionResult(ExecutionState.VERIFIED, "ok", 1, (), "audit")

    monkeypatch.setattr("autonomous_agent.runtime.WorkspaceConnector", FakeConnector)
    monkeypatch.setattr("autonomous_agent.runtime.AutonomousTaskCore", FakeCore)
    task = "Use the canonical local workspace shell to run exactly this safe validation command: python -m py_compile autonomous_agent/runtime.py."
    result = run_task(task, root=tmp_path, audit_path=tmp_path / "audit.jsonl", journal_path=tmp_path / "journal.jsonl", execution_id="workspace-runtime-test")
    assert result.state is ExecutionState.VERIFIED
    assert captured["root"] == tmp_path
    assert isinstance(captured["connector"], FakeConnector)
    assert captured["request"]["workspace.shell"]["argv"] == ("python", "-m", "py_compile", "autonomous_agent/runtime.py")
