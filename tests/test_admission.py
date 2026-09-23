import hashlib
from pathlib import Path

from autonomous_agent.admission import AdmissionRequest, evaluate_admission
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState, _authorization_digest, execute_plan
from autonomous_agent.production_audit import AuditFinding, run_production_audit
from autonomous_agent.readiness import ReadinessCheck, evaluate_readiness
from autonomous_agent.task_planner import plan_task

def _evidence():
    readiness = evaluate_readiness([ReadinessCheck("canonical-tests", True, "verified")])
    audit = run_production_audit(
        [AuditFinding(area, True, "verified") for area in (
            "concurrency", "replay", "queue_bounds", "prompt_injection",
            "secret_redaction", "shell_safety", "approval", "ci", "external_data",
        )]
    )
    return readiness, audit

def test_n46_admission_accepts_matching_evidence():
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    readiness, audit = _evidence()
    auth_digest = _authorization_digest([Capability.INSPECT], False, plan)
    task_digest = hashlib.sha256(plan.task.encode()).hexdigest()
    request = AdmissionRequest(
        task_id="n46-task",
        execution_id="n46-exec",
        expected_task_digest=task_digest,
        expected_authorization_digest=auth_digest,
    )
    decision = evaluate_admission(
        request,
        actual_task_digest=task_digest,
        actual_authorization_digest=auth_digest,
        readiness=readiness,
        production_audit=audit,
    )
    assert decision.admitted
    assert decision.reason == "canonical admission gates satisfied"
    assert len(decision.digest) == 64

def test_n46_admission_fails_on_readiness_failure():
    readiness = evaluate_readiness([ReadinessCheck("canonical-tests", False, "failed")])
    _, audit = _evidence()
    request = AdmissionRequest("n46-task", "n46-exec", "0" * 64, "1" * 64)
    decision = evaluate_admission(
        request,
        actual_task_digest="0" * 64,
        actual_authorization_digest="1" * 64,
        readiness=readiness,
        production_audit=audit,
    )
    assert not decision.admitted
    assert "readiness gate" in decision.reason

def test_n46_admission_fails_on_audit_failure():
    readiness, _ = _evidence()
    audit = run_production_audit([AuditFinding("ci", False, "failed")])
    request = AdmissionRequest("n46-task", "n46-exec", "0" * 64, "1" * 64)
    decision = evaluate_admission(
        request,
        actual_task_digest="0" * 64,
        actual_authorization_digest="1" * 64,
        readiness=readiness,
        production_audit=audit,
    )
    assert not decision.admitted
    assert "production audit" in decision.reason

def test_n46_admission_binds_task_and_authorization_digests():
    readiness, audit = _evidence()
    request = AdmissionRequest("n46-task", "n46-exec", "0" * 64, "1" * 64)
    decision = evaluate_admission(
        request,
        actual_task_digest="2" * 64,
        actual_authorization_digest="1" * 64,
        readiness=readiness,
        production_audit=audit,
    )
    assert not decision.admitted
    assert "task digest" in decision.reason

def test_n46_admission_requires_approval_for_side_effects():
    readiness, audit = _evidence()
    request = AdmissionRequest("n46-task", "n46-exec", "0" * 64, "1" * 64, side_effects=True)
    decision = evaluate_admission(
        request,
        actual_task_digest="0" * 64,
        actual_authorization_digest="1" * 64,
        readiness=readiness,
        production_audit=audit,
    )
    assert not decision.admitted
    assert "explicit approval" in decision.reason

def test_n46_canonical_execution_enforces_admission_gate(tmp_path: Path):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    readiness, audit = _evidence()
    task_digest = hashlib.sha256(plan.task.encode()).hexdigest()
    auth_digest = _authorization_digest([Capability.INSPECT], False, plan)

    blocked = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "blocked.jsonl",
        execution_id="n46-blocked",
        admission_request=AdmissionRequest(
            "n46-task",
            "n46-blocked",
            task_digest,
            auth_digest,
        ),
        readiness_report=evaluate_readiness([ReadinessCheck("canonical-tests", False, "failed")]),
        production_audit=audit,
    )
    assert blocked.state is ExecutionState.BLOCKED
    assert "readiness gate" in blocked.reason

    verified = execute_plan(
        plan,
        tmp_path / "ok",
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "ok" / "execution.jsonl",
        execution_id="n46-verified",
        admission_request=AdmissionRequest(
            "n46-task",
            "n46-verified",
            task_digest,
            auth_digest,
        ),
        readiness_report=readiness,
        production_audit=audit,
    )
    assert verified.state is ExecutionState.VERIFIED
