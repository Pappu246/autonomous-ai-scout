from __future__ import annotations

import hashlib

from autonomous_agent.admission import AdmissionRequest, evaluate_admission
from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital import build_agent
from autonomous_agent.digital.domains import CapabilityDomain, DomainPhase, all_domains, reserved_domains
from autonomous_agent.execution_engine import _authorization_digest
from autonomous_agent.production_audit import AuditFinding, run_production_audit
from autonomous_agent.readiness import ReadinessCheck, evaluate_readiness
from autonomous_agent.task_planner import plan_task

REQUIRED_AUDIT_AREAS = (
    "concurrency", "replay", "queue_bounds", "prompt_injection",
    "secret_redaction", "shell_safety", "approval", "ci", "external_data",
)

def _passing_evidence():
    readiness = evaluate_readiness([
        ReadinessCheck("digital-catalog", True, "catalog assembled from registered capabilities"),
        ReadinessCheck("canonical-ci", True, "repository CI evidence is green"),
        ReadinessCheck("admission-contract", True, "N46 admission contract verified"),
    ])
    audit = run_production_audit([
        AuditFinding(area, True, "bounded control verified")
        for area in REQUIRED_AUDIT_AREAS
    ])
    return readiness, audit

def test_phase12_active_domains_are_not_stale_reserved_claims():
    domains = all_domains()
    assert domains
    statuses = {item.domain: item.phase for item in domains}
    assert statuses[CapabilityDomain.APPLICATION] is DomainPhase.ACTIVE
    assert statuses[CapabilityDomain.DOCUMENTS] is DomainPhase.ACTIVE
    assert reserved_domains() == ()

def test_phase12_catalog_reports_registered_capabilities_for_active_domains(tmp_path):
    from autonomous_agent.filesystem_workspace import WorkspaceConnector
    agent = build_agent(root=tmp_path, connectors={'workspace': WorkspaceConnector(tmp_path)})
    statuses = {item['domain']: item for item in agent.domain_status()}
    assert statuses['application']['phase'] == 'active'
    assert statuses['documents']['phase'] == 'active'
    assert statuses['filesystem']['usable'] is True
    assert statuses['testing']['usable'] is True
    assert all('usable' in item for item in statuses.values())

def test_phase12_admission_binds_readiness_audit_and_approval():
    plan = plan_task('inspect repository', granted=[Capability.INSPECT])
    readiness, audit = _passing_evidence()
    task_digest = hashlib.sha256(plan.task.encode()).hexdigest()
    auth_digest = _authorization_digest([Capability.INSPECT], False, plan)
    request = AdmissionRequest(
        task_id='phase12-task', execution_id='phase12-exec',
        expected_task_digest=task_digest, expected_authorization_digest=auth_digest,
    )
    decision = evaluate_admission(
        request, actual_task_digest=task_digest,
        actual_authorization_digest=auth_digest, actual_execution_id='phase12-exec',
        actual_side_effects=False, actual_explicitly_approved=False,
        readiness=readiness, production_audit=audit,
    )
    assert decision.admitted
    assert decision.reason == 'canonical admission gates satisfied'
    assert len(decision.digest) == 64

def test_phase12_side_effects_remain_approval_gated():
    readiness, audit = _passing_evidence()
    request = AdmissionRequest(
        task_id='phase12-write', execution_id='phase12-write-exec',
        expected_task_digest='0' * 64, expected_authorization_digest='1' * 64,
        side_effects=True, explicitly_approved=False,
    )
    decision = evaluate_admission(
        request, actual_task_digest='0' * 64,
        actual_authorization_digest='1' * 64, actual_execution_id='phase12-write-exec',
        actual_side_effects=True, actual_explicitly_approved=False,
        readiness=readiness, production_audit=audit,
    )
    assert not decision.admitted
    assert 'explicit approval' in decision.reason
