from dataclasses import dataclass

from autonomous_agent.task_orchestrator import OrchestrationState, StructuredTask, TaskOrchestrator
from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import REGISTRY


def test_deterministic_digest_and_secret_redaction():
    a = TaskOrchestrator().orchestrate(StructuredTask("inspect token=supersecret", project="demo"), granted=[Capability.INSPECT, Capability.READ_FILE])
    b = TaskOrchestrator().orchestrate(StructuredTask("inspect token=anothersecret", project="demo"), granted=[Capability.INSPECT, Capability.READ_FILE])
    assert a.objective == b.objective == "inspect [REDACTED]"
    assert a.task_digest == b.task_digest
    assert "supersecret" not in str(a.safe_dict)


def test_unknown_automation_fails_closed():
    report = TaskOrchestrator().orchestrate("automate a browser action")
    assert report.state is OrchestrationState.BLOCKED
    assert report.selected_tools == ()


def test_unauthorized_tool_is_blocked_by_existing_policy():
    report = TaskOrchestrator().orchestrate("inspect repository")
    assert report.state is OrchestrationState.BLOCKED
    assert report.steps
    assert all(s.status is OrchestrationState.BLOCKED for s in report.steps)


def test_authorized_plan_is_only_prepared_not_executed():
    report = TaskOrchestrator().orchestrate("inspect repository", granted=[Capability.INSPECT, Capability.READ_FILE])
    assert report.state is OrchestrationState.AUTHORIZED
    result = TaskOrchestrator().execute(report)
    assert result.state is OrchestrationState.BLOCKED


def test_connector_discovery_can_only_constrain_tools():
    @dataclass(frozen=True)
    class Connector:
        identity: str
        enabled: bool
        registered_tools: tuple[str, ...]

    class Discovery:
        def list(self):
            return (Connector("disabled", False, ("github.inspect",)),)
        def get(self, identity):
            return None
        def resolve_tool(self, identity, tool_name):
            return None

    report = TaskOrchestrator(connector_registry=Discovery()).orchestrate("inspect repository", granted=[Capability.INSPECT, Capability.READ_FILE])
    assert report.state is OrchestrationState.BLOCKED


def test_report_contains_no_raw_credentials_or_executor_authority():
    report = TaskOrchestrator().orchestrate("inspect api_key=abc123")
    assert "abc123" not in str(report.safe_dict)
    assert not hasattr(report, "executor")
    assert REGISTRY.get("github.inspect") is not None
