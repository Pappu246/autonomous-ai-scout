from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_orchestrator import OrchestrationState, StructuredTask, TaskOrchestrator


class _Executor:
    def __init__(self, result=True, interrupt=False):
        self.calls = []
        self.result = result
        self.interrupt = interrupt

    def execute_authorized(self, step_id, tool_name, task_digest):
        self.calls.append((step_id, tool_name, task_digest))
        if self.interrupt:
            raise InterruptedError
        return self.result


class _Memory:
    def __init__(self):
        self.retrieved = []
        self.stored = []

    def retrieve(self, project, task_digest):
        self.retrieved.append((project, task_digest))
        return ({"finding": "stale evidence", "authorized": True},)

    def store_safe(self, evidence):
        self.stored.append(evidence)


class _Connector:
    def __init__(self, identity, enabled, registered_tools):
        self.identity = identity
        self.enabled = enabled
        self.registered_tools = registered_tools


class _Discovery:
    def __init__(self, connector):
        self.connector = connector

    def list(self):
        return (self.connector,)

    def get(self, identity):
        return self.connector if identity == self.connector.identity else None

    def resolve_tool(self, identity, tool_name):
        if identity != self.connector.identity or not self.connector.enabled:
            return None
        return object() if tool_name in self.connector.registered_tools else None


def _safe_grants():
    return (Capability.INSPECT, Capability.READ_FILE)


def test_successful_orchestration_uses_existing_executor_only():
    memory = _Memory()
    executor = _Executor()
    orchestrator = TaskOrchestrator(memory=memory)
    report = orchestrator.orchestrate("inspect the project", granted=_safe_grants())
    assert report.state is OrchestrationState.AUTHORIZED
    completed = orchestrator.execute(report, executor)
    assert completed.state is OrchestrationState.SUCCEEDED
    assert len(executor.calls) == len(report.steps)
    assert memory.stored[0]["task_digest"] == report.task_digest


def test_unauthorized_plan_never_reaches_executor():
    executor = _Executor()
    report = TaskOrchestrator().orchestrate("inspect the project")
    assert report.state is OrchestrationState.BLOCKED
    assert TaskOrchestrator().execute(report, executor).state is OrchestrationState.BLOCKED
    assert executor.calls == []


def test_memory_is_observational_only():
    memory = _Memory()
    report = TaskOrchestrator(memory=memory).orchestrate("inspect the project")
    assert report.state is OrchestrationState.BLOCKED
    assert memory.retrieved


def test_secret_material_is_not_retained():
    safe, plan = TaskOrchestrator().plan(StructuredTask("inspect repo token=SUPERSECRET"), granted=_safe_grants())
    assert "SUPERSECRET" not in safe.task
    assert "SUPERSECRET" not in plan.audit.plan_digest
    assert "SUPERSECRET" not in str(safe.metadata)


def test_deterministic_task_and_plan_digests():
    first = TaskOrchestrator().orchestrate("  inspect   the project  ", granted=_safe_grants())
    second = TaskOrchestrator().orchestrate("inspect the project", granted=_safe_grants())
    assert first.task_digest == second.task_digest
    assert first.plan_digest == second.plan_digest


def test_interruption_never_replays_automatically():
    executor = _Executor(interrupt=True)
    report = TaskOrchestrator().orchestrate("inspect the project", granted=_safe_grants())
    interrupted = TaskOrchestrator().execute(report, executor)
    assert interrupted.state is OrchestrationState.INTERRUPTED
    assert len(executor.calls) == 1


def test_permanent_network_denial_is_preserved():
    report = TaskOrchestrator().orchestrate("research the requested information", granted=(Capability.NETWORK,))
    assert report.state is OrchestrationState.BLOCKED
    assert report.approval_required is False


def test_disabled_connector_constrains_execution():
    discovery = _Discovery(_Connector("disabled", False, ("github.inspect", "filesystem.read")))
    report = TaskOrchestrator(connector_registry=discovery).orchestrate("inspect repository", granted=_safe_grants())
    assert report.state is OrchestrationState.BLOCKED


def test_connector_cannot_add_unregistered_tool_authority():
    discovery = _Discovery(_Connector("future", True, ("not-a-real-tool",)))
    report = TaskOrchestrator(connector_registry=discovery).orchestrate("inspect repository", granted=_safe_grants())
    assert report.state is OrchestrationState.BLOCKED


def test_report_is_secret_safe_and_contains_only_references():
    report = TaskOrchestrator().orchestrate("inspect api_key=abc123")
    rendered = str(report.safe_dict)
    assert "abc123" not in rendered
    assert not hasattr(report, "executor")
