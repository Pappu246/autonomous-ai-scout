"""Phase 1: the canonical execution lifecycle, verification and resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital.authorization import CapabilityAuthorizationBroker
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import (
    CapabilityError,
    CapabilityRequest,
    RetryPolicy,
)
from autonomous_agent.digital.planner import CapabilityPlanner
from autonomous_agent.digital.provider import SandboxCapabilityExecutor
from autonomous_agent.digital.runtime import (
    DigitalAgentRuntime,
    DigitalResultState,
)
from autonomous_agent.execution_audit import append_execution_record
from autonomous_agent.filesystem_workspace import WorkspaceConnector
from autonomous_agent.prompt_injection_guard import TrustLevel

from .digital_support import (
    FlakyExecutor,
    NoopExecutor,
    RecordingExecutor,
    StubObserver,
    build_capability,
    build_runtime,
    make_result,
)
from .digital_support import DECLARATIONS_BY_ID


TWO_STEP_GOAL = "list the files in the docs folder and read the file notes.txt"


def run(runtime, goal, tmp_path, *, execution_id="run-1", **kwargs):
    return runtime.run(
        goal,
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id=execution_id,
        **kwargs,
    )


# -- safe execution and verification ---------------------------------------


def test_read_only_goal_executes_and_verifies(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.VERIFIED
    assert result.verified
    assert executor.calls == [("filesystem.list", {"path": "."})]
    assert result.steps[0].verified
    assert result.granted == (Capability.FILES_WORKSPACE,)


def test_verification_requires_observable_evidence(tmp_path: Path):
    """A successful call with no evidence is a no-op and is never VERIFIED."""
    runtime, _ = build_runtime(("filesystem:list",), NoopExecutor())
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.FAILED
    assert not result.verified
    assert "no observable evidence" in result.reason or "evidence" in result.reason


def test_boundary_failure_is_reported_as_failed(tmp_path: Path):
    executor = RecordingExecutor({"filesystem.list": make_result(success=False, output="denied")})
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.FAILED
    assert not result.verified


def test_bounded_retry_recovers_from_a_transient_failure(tmp_path: Path):
    executor = FlakyExecutor({"filesystem.list": 1})
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.VERIFIED
    assert result.attempts == 2
    assert executor.call_count("filesystem.list") == 2


def test_bounded_retry_stops_at_the_declared_budget(tmp_path: Path):
    executor = FlakyExecutor({"filesystem.list": 99})
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.FAILED
    # filesystem:list declares RetryPolicy(max_attempts=2)
    assert executor.call_count("filesystem.list") == 2
    assert result.attempts == 2


def test_retry_policy_is_bounded():
    with pytest.raises(CapabilityError):
        RetryPolicy(max_attempts=99)
    with pytest.raises(CapabilityError):
        RetryPolicy(max_attempts=1, backoff_seconds=5)


def test_a_rejected_post_condition_is_never_verified(tmp_path: Path):
    """Boundary success is not enough when an observer rejects the outcome."""
    observer = StubObserver(observed=False, evidence={})
    runtime, _ = build_runtime(("filesystem:write",), RecordingExecutor(), observers={"filesystem:write": observer})
    result = run(
        runtime,
        "write file notes.txt",
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=True,
        requests={"filesystem:write": {"path": "notes.txt", "content": "hi"}},
    )
    assert result.state is DigitalResultState.FAILED
    assert observer.calls >= 1


def test_untrusted_origin_blocks_a_side_effect(tmp_path: Path):
    runtime, _ = build_runtime(("filesystem:write",), RecordingExecutor())
    result = run(
        runtime,
        "write file notes.txt",
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        origin_trust=TrustLevel.EXTERNAL,
        requests={"filesystem:write": {"path": "notes.txt", "content": "hi"}},
    )
    assert result.state is DigitalResultState.REQUIRES_APPROVAL


# -- approval boundaries ---------------------------------------------------


def test_side_effect_is_not_executed_without_approval(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:write",), executor)
    result = run(
        runtime,
        "write file notes.txt",
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        requests={"filesystem:write": {"path": "notes.txt", "content": "hi"}},
    )
    assert result.state is DigitalResultState.REQUIRES_APPROVAL
    assert executor.calls == []
    assert not (tmp_path / "notes.txt").exists()


def test_approved_side_effect_executes(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:write",), executor)
    result = run(
        runtime,
        "write file notes.txt",
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=True,
        requests={"filesystem:write": {"path": "notes.txt", "content": "hi"}},
    )
    assert result.state is DigitalResultState.VERIFIED
    assert executor.calls[0][1]["approved"] is True


def test_approval_flag_is_supplied_by_the_runtime_not_the_goal(tmp_path: Path):
    """A model-supplied ``approved`` flag cannot unlock a side effect."""
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:write",), executor)
    result = run(
        runtime,
        "write file notes.txt",
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        requests={
            "filesystem:write": {"path": "notes.txt", "content": "hi", "approved": True}
        },
    )
    assert result.state is DigitalResultState.REQUIRES_APPROVAL
    assert executor.calls == []


def test_missing_sandbox_blocks_execution(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        sandbox_available=False,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.BLOCKED
    assert executor.calls == []


# -- unknown capability fail-closed ----------------------------------------


class ForgetfulCatalog(CapabilityCatalog):
    """Reports a capability as missing after planning has selected it."""

    def __init__(self, capabilities, *, forget: str) -> None:
        super().__init__(capabilities)
        self._forget = forget

    def get(self, capability_id):
        if capability_id == self._forget:
            return None
        return super().get(capability_id)


def test_capability_that_disappears_before_execution_blocks_the_run(tmp_path: Path):
    executor = RecordingExecutor()
    catalog = ForgetfulCatalog(
        (build_capability("filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], executor),),
        forget="filesystem:list",
    )
    runtime = DigitalAgentRuntime(catalog, planner=CapabilityPlanner(catalog))
    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.BLOCKED
    assert "not registered" in result.reason
    assert executor.calls == []


def test_reserved_domain_goal_is_blocked_not_faked(tmp_path: Path):
    runtime, _ = build_runtime(("filesystem:list",), RecordingExecutor())
    result = run(runtime, "extract text from the pdf report", tmp_path)
    assert result.state is DigitalResultState.BLOCKED
    assert "not yet registered" in result.reason


def test_computer_control_goal_is_blocked(tmp_path: Path):
    runtime, _ = build_runtime(("filesystem:list",), RecordingExecutor())
    result = run(runtime, "open an application called the calculator", tmp_path)
    assert result.state is DigitalResultState.BLOCKED


def test_execution_identity_is_required(tmp_path: Path):
    runtime, _ = build_runtime(("filesystem:list",), RecordingExecutor())
    result = runtime.run(
        "list the files in the docs folder",
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="   ",
    )
    assert result.state is DigitalResultState.BLOCKED


# -- multi-capability planning and ordering --------------------------------


def test_multi_capability_plan_executes_every_step_in_stage_order(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list", "filesystem:read"), executor)
    result = run(
        runtime,
        TWO_STEP_GOAL,
        tmp_path,
        requests={
            "filesystem:list": {"path": "docs"},
            "filesystem:read": {"path": "notes.txt"},
        },
    )
    assert result.state is DigitalResultState.VERIFIED
    assert [name for name, _ in executor.calls] == ["filesystem.list", "filesystem.read"]
    assert [step.step_id for step in result.steps] == ["step-1", "step-2"]
    assert all(step.verified for step in result.steps)


def test_second_step_failure_fails_the_whole_run(tmp_path: Path):
    executor = RecordingExecutor({"filesystem.read": make_result(success=False, output="missing")})
    runtime, _ = build_runtime(("filesystem:list", "filesystem:read"), executor)
    result = run(
        runtime,
        TWO_STEP_GOAL,
        tmp_path,
        requests={
            "filesystem:list": {"path": "docs"},
            "filesystem:read": {"path": "notes.txt"},
        },
    )
    assert result.state is DigitalResultState.FAILED
    assert result.steps[0].verified
    assert not result.steps[1].verified


def test_missing_required_argument_fails_closed(tmp_path: Path):
    """A capability whose required arguments are absent never executes."""
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("os_shell:run",), executor)
    result = run(runtime, "run command py_compile on main.py", tmp_path, requests={})
    assert result.state is DigitalResultState.BLOCKED
    assert "missing required arguments: argv" in result.reason
    assert executor.calls == []


def test_unknown_argument_is_rejected_by_the_registered_schema(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("os_shell:run",), executor)
    result = run(
        runtime,
        "run command py_compile on main.py",
        tmp_path,
        requests={"os_shell:run": {"argv": ["python", "-c", "print(1)"], "extra": 1}},
    )
    assert result.state is DigitalResultState.BLOCKED
    assert "unknown arguments" in result.reason
    assert executor.calls == []


# -- checkpoint and resume -------------------------------------------------


def test_resume_does_not_replay_verified_work(tmp_path: Path):
    failing = RecordingExecutor({"filesystem.read": make_result(success=False, output="gone")})
    runtime, _ = build_runtime(("filesystem:list", "filesystem:read"), failing)
    checkpoint = tmp_path / "checkpoint.json"
    requests = {
        "filesystem:list": {"path": "docs"},
        "filesystem:read": {"path": "notes.txt"},
    }

    first = runtime.run(
        TWO_STEP_GOAL,
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="resume-1",
        requests=requests,
        checkpoint_path=checkpoint,
    )
    assert first.state is DigitalResultState.FAILED
    assert failing.call_count("filesystem.list") == 1

    healthy = RecordingExecutor()
    runtime2, _ = build_runtime(("filesystem:list", "filesystem:read"), healthy)
    second = runtime2.run(
        TWO_STEP_GOAL,
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="resume-1",
        requests=requests,
        checkpoint_path=checkpoint,
        resume=True,
    )
    assert second.state is DigitalResultState.VERIFIED
    assert second.resumed_steps == ("step-1",)
    assert healthy.call_count("filesystem.list") == 0  # verified work was not replayed
    assert healthy.call_count("filesystem.read") == 1
    assert second.steps[0].resumed


def test_resume_without_a_checkpoint_replays_everything(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list",), executor)
    run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    runtime2, _ = build_runtime(("filesystem:list",), executor)
    second = run(
        runtime2,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
        resume=True,
    )
    assert second.resumed_steps == ()
    assert executor.call_count("filesystem.list") == 2


def test_resume_refuses_progress_from_a_different_plan(tmp_path: Path):
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list", "filesystem:read"), executor)
    checkpoint = tmp_path / "checkpoint.json"
    first = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "docs"}},
        checkpoint_path=checkpoint,
    )
    assert first.state is DigitalResultState.VERIFIED

    second = run(
        runtime,
        TWO_STEP_GOAL,
        tmp_path,
        execution_id="run-1",
        requests={
            "filesystem:list": {"path": "docs"},
            "filesystem:read": {"path": "notes.txt"},
        },
        checkpoint_path=checkpoint,
        resume=True,
    )
    assert second.resumed_steps == ()
    assert executor.call_count("filesystem.list") == 2


def test_interrupted_execution_requires_recovery(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    append_execution_record(
        audit,
        {
            "execution_id": "crash-1",
            "event": "capability_started",
            "state": "running",
            "capability_id": "filesystem:list",
            "tool_name": "filesystem.list",
        },
    )
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list",), executor)
    result = runtime.run(
        "list the files in the docs folder",
        root=tmp_path,
        audit_path=audit,
        execution_id="crash-1",
        requests={"filesystem:list": {"path": "."}},
        resume=True,
    )
    assert result.state is DigitalResultState.RECOVERY_REQUIRED
    assert executor.calls == []


def test_tampered_audit_blocks_the_run(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list",), executor)
    run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        requests={"filesystem:list": {"path": "."}},
    )
    lines = audit.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["state"] = "verified"
    lines[0] = json.dumps(record, sort_keys=True)
    audit.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = run(
        runtime,
        "list the files in the docs folder",
        tmp_path,
        execution_id="run-2",
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.BLOCKED
    assert "audit chain is invalid" in result.reason


def test_every_step_is_written_to_the_hash_chained_audit(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    executor = RecordingExecutor()
    runtime, _ = build_runtime(("filesystem:list", "filesystem:read"), executor)
    runtime.run(
        TWO_STEP_GOAL,
        root=tmp_path,
        audit_path=audit,
        execution_id="audit-1",
        requests={
            "filesystem:list": {"path": "docs"},
            "filesystem:read": {"path": "notes.txt"},
        },
    )
    from autonomous_agent.execution_audit import verify_execution_audit

    assert verify_execution_audit(audit)
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert {item["event"] for item in events} >= {
        "capability_started",
        "capability_result",
        "run_verified",
    }
    verified = [
        item for item in events if item["event"] == "capability_result"
    ]
    assert len(verified) == 2
    assert all(item["verification"] == "verified" for item in verified)


# -- credentials -----------------------------------------------------------


def test_raw_credential_material_is_rejected():
    with pytest.raises(CapabilityError):
        CapabilityRequest(
            "email:send", "email.send", {}, credential_reference="token=abc123"
        )
    with pytest.raises(CapabilityError):
        CapabilityRequest("email:send", "email.send", {}, credential_reference="not-a-credref")


def test_bounded_credential_references_are_accepted():
    request = CapabilityRequest(
        "email:send", "email.send", {}, credential_reference="credref:gmail/user/messages"
    )
    assert request.credential_reference == "credref:gmail/user/messages"


def test_capability_descriptor_refuses_credential_material():
    from autonomous_agent.digital.contract import CapabilityDescriptor
    from autonomous_agent.digital.domains import CapabilityDomain

    with pytest.raises(CapabilityError):
        CapabilityDescriptor(
            capability_id="email:send",
            domain=CapabilityDomain.EMAIL,
            tool_name="email.send",
            description="api_key=supersecret",
            capability="email",
            risk="critical",
            read_write="high_risk_write",
            network="required",
            approval="human_review",
            sandbox="required",
            audit="required",
            safe_autonomous=False,
        )


# -- real end-to-end through the existing workspace connector --------------


def test_end_to_end_filesystem_workflow_verifies_real_effects(tmp_path: Path):
    from autonomous_agent.digital import build_agent

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.txt").write_text("hello\n", encoding="utf-8")
    connector = WorkspaceConnector(tmp_path)
    agent = build_agent(root=tmp_path, connectors={"workspace": connector})
    audit = tmp_path / "audit.jsonl"

    listed = agent.run(
        "list the files in the docs folder",
        root=tmp_path,
        audit_path=audit,
        execution_id="e2e-1",
        requests={"filesystem:list": {"path": "docs"}},
    )
    assert listed.state is DigitalResultState.VERIFIED
    assert "notes.txt" in str(listed.steps[0].observation) or listed.steps[0].verified

    written = agent.run(
        "write file report.md",
        root=tmp_path,
        audit_path=audit,
        execution_id="e2e-2",
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=True,
        requests={"filesystem:write": {"path": "report.md", "content": "# Report\n"}},
    )
    assert written.state is DigitalResultState.VERIFIED
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Report\n"
    assert "independent re-read" in written.steps[0].observation

    from autonomous_agent.execution_audit import verify_execution_audit

    assert verify_execution_audit(audit)


def test_real_write_that_does_not_land_is_not_verified(tmp_path: Path):
    """The observer re-reads the workspace, so a lost write cannot pass."""
    (tmp_path / "docs").mkdir()
    connector = WorkspaceConnector(tmp_path)

    class VanishingConnector:
        """Accepts the write, then reports different content on re-read."""

        def __init__(self, inner) -> None:
            self._inner = inner
            self._reads = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def read(self, relative):
            self._reads += 1
            evidence = self._inner.read(relative)
            return type(evidence)(
                evidence.operation,
                evidence.relative_path,
                "deadbeef",
                "tampered content",
                evidence.entries,
                evidence.redacted,
            )

    vanishing = VanishingConnector(connector)
    from autonomous_agent.digital import build_agent

    agent = build_agent(root=tmp_path, connectors={"workspace": vanishing})
    result = agent.run(
        "write file report.md",
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="e2e-3",
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=True,
        requests={"filesystem:write": {"path": "report.md", "content": "# Report\n"}},
    )
    assert result.state is DigitalResultState.FAILED
    assert "does not match" in result.reason


# -- approval flag plumbing through the real sandbox -----------------------


class _RecordingMailConnector:
    """Minimal stand-in exposing the send() surface the sandbox calls."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(self, **kwargs):
        self.sent.append(dict(kwargs))
        outer = self

        class _Evidence:
            @staticmethod
            def safe_dict():
                return {"operation": "send", "message_id": "m-1", "approved": kwargs.get("approved")}

        del outer
        return _Evidence()


def test_approval_flag_reaches_the_connector_only_from_the_runtime(tmp_path: Path):
    """The connector-visible ``approved`` flag is runtime state, not an argument."""
    connector = _RecordingMailConnector()
    executor = SandboxCapabilityExecutor(tmp_path, connectors={"gmail": connector})
    capability = build_capability("email:send", DECLARATIONS_BY_ID["email:send"], executor)
    catalog = CapabilityCatalog((capability,))
    broker = CapabilityAuthorizationBroker(catalog)
    runtime = DigitalAgentRuntime(
        catalog, planner=CapabilityPlanner(catalog, authorization=broker), authorization=broker
    )

    refused = run(
        runtime,
        "send email to bob",
        tmp_path,
        granted=[Capability.EMAIL],
        requests={
            "email:send": {
                "to": "bob@example.com",
                "subject": "hi",
                "body": "hello",
                "idempotency_key": "k-1",
                "approved": True,  # model-supplied; must be ignored
            }
        },
    )
    assert refused.state is DigitalResultState.REQUIRES_APPROVAL
    assert connector.sent == []

    approved = run(
        runtime,
        "send email to bob",
        tmp_path,
        execution_id="run-2",
        granted=[Capability.EMAIL],
        explicitly_approved=True,
        requests={
            "email:send": {
                "to": "bob@example.com",
                "subject": "hi",
                "body": "hello",
                "idempotency_key": "k-1",
            }
        },
    )
    assert approved.state is DigitalResultState.VERIFIED
    assert len(connector.sent) == 1
    assert connector.sent[0]["approved"] is True


# -- the shell capability stays read-only and allowlisted ------------------


def test_shell_capability_cannot_run_arbitrary_commands(tmp_path: Path):
    from autonomous_agent.workspace_shell import ControlledWorkspaceShell

    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    shell = ControlledWorkspaceShell(tmp_path)
    executor = SandboxCapabilityExecutor(tmp_path, connectors={"workspace": shell})
    capability = build_capability("os_shell:run", DECLARATIONS_BY_ID["os_shell:run"], executor)
    catalog = CapabilityCatalog((capability,))
    broker = CapabilityAuthorizationBroker(catalog)
    runtime = DigitalAgentRuntime(
        catalog, planner=CapabilityPlanner(catalog, authorization=broker), authorization=broker
    )

    allowed = run(
        runtime,
        "run command ls",
        tmp_path,
        requests={"os_shell:run": {"argv": ["ls"]}},
    )
    assert allowed.state is DigitalResultState.VERIFIED

    refused = run(
        runtime,
        "run command ls",
        tmp_path,
        execution_id="run-2",
        requests={"os_shell:run": {"argv": ["rm", "-rf", str(tmp_path)]}},
    )
    assert refused.state is DigitalResultState.FAILED
    assert "allowlist" in refused.reason
    assert (tmp_path / "main.py").exists()

    metacharacters = run(
        runtime,
        "run command ls",
        tmp_path,
        execution_id="run-3",
        requests={"os_shell:run": {"argv": ["ls", ";", "rm", "-rf", "."]}},
    )
    assert metacharacters.state is DigitalResultState.FAILED
    assert (tmp_path / "main.py").exists()
