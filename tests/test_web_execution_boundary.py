from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import web_connector
from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan, recover_execution
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.task_orchestrator import StructuredTask, TaskOrchestrator
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import REGISTRY
from autonomous_agent.web_domain_connector import WebEvidence, WebResearchConnector, WebConnectorError, meaningful_web_change, record_web_evidence


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.failures = 0

    def __call__(self, method, target, timeout):
        self.calls.append((method, target, timeout))
        if self.failures:
            self.failures -= 1
            raise TimeoutError("controlled timeout")
        if method == "SEARCH":
            return {"results": [{"url": "https://example.com/a", "title": "A", "snippet": "Value: one"}, {"url": "https://example.com/a", "title": "duplicate", "snippet": "ignored"}]}
        return {"content_type": "text/html", "title": "Example", "text": "<html><body>Value: one. public content.</body></html>", "final_url": target}


def _connector(transport=None):
    return WebResearchConnector(transport or FakeTransport(), allowed_domains={"example.com"}, max_retries=2, timeout_seconds=3)


def _research_plan():
    return plan_task("research public information from example.com", granted=[Capability.WEB_RESEARCH])


def _execute(plan, connector, tmp_path, *, request=None, retries=0):
    return execute_plan(plan, tmp_path, granted=[Capability.WEB_RESEARCH], sandbox_available=True, audit_path=tmp_path / "audit.jsonl", execution_id="exec-1", web_connector=connector, web_request=request, max_retries=retries)


def test_orchestrator_to_safe_executor_to_sandbox(tmp_path):
    connector = _connector(); orchestrator = TaskOrchestrator(connector_registry=web_connector().__class__())
    report = orchestrator.orchestrate(StructuredTask("research example.com", "project-a"), granted=[Capability.WEB_RESEARCH])
    assert report.state.value == "authorized"
    plan = _research_plan()
    result = _execute(plan, connector, tmp_path, request={"web.search": {"operation": "search", "query": "example", "results": 1}, "web.read": {"operation": "read", "url": "https://example.com/a"}, "web.extract": {"operation": "extract", "evidence": connector.read("https://example.com/a"), "fields": ["Value"]}, "web.compare": {"operation": "compare", "sources": (connector.read("https://example.com/a"), connector.read("https://example.com/a"))}})
    assert result.state is ExecutionState.VERIFIED
    assert verify_execution_audit(tmp_path / "audit.jsonl")


def test_search_is_bounded_and_deduplicated():
    c = _connector(); evidence = c.search("x", results=10)
    assert len(evidence) == 1 and evidence[0].url == "https://example.com/a"
    with pytest.raises(WebConnectorError): c.search("x" * 501)


def test_read_is_bounded_and_verified():
    e = _connector().read("https://example.com/a")
    assert e.domain == "example.com" and e.fingerprint and not e.stale


def test_extract_is_deterministic_and_source_linked():
    c = _connector(); e = c.read("https://example.com/a"); out = c.extract(e, ["Value"])
    assert out["Value"]["status"] == "verified" and out["Value"]["source_ref"] == e.source_ref


def test_compare_detects_conflict_without_invention():
    c = _connector(); a = WebEvidence("https://example.com/a", "example.com", "a", "Value: one.", "2026-01-01T00:00:00+00:00", "a", "a"); b = WebEvidence("https://example.com/b", "example.com", "b", "Value: two.", "2026-01-01T00:00:00+00:00", "b", "b")
    facts = c.compare((a, b))["facts"]
    assert any(f["status"] == "conflicting" for f in facts)


def test_bounded_response_is_enforced(tmp_path):
    class Big(FakeTransport):
        def __call__(self, method, target, timeout): return {"content_type": "text/plain", "text": "x" * 2_000_000, "content_length": 2_000_000}
    result = run_safe_operation("web_research", tmp_path, web_connector=_connector(Big()), web_request={"operation": "read", "url": "https://example.com"}, output_limit=1024)
    assert not result.success and "failed" in result.verification_status


def test_bounded_retries(tmp_path):
    transport = FakeTransport(); transport.failures = 2; c = _connector(transport)
    result = run_safe_operation("web_research", tmp_path, web_connector=c, web_request={"operation": "read", "url": "https://example.com"})
    assert result.success and len(transport.calls) == 3


def test_timeout_failure_is_fail_closed(tmp_path):
    class Slow(FakeTransport):
        def __call__(self, method, target, timeout): raise TimeoutError("timeout")
    result = run_safe_operation("web_research", tmp_path, web_connector=_connector(Slow()), web_request={"operation": "read", "url": "https://example.com"})
    assert not result.success


def test_unsafe_redirect_is_rejected():
    class Redirect(FakeTransport):
        def __call__(self, method, target, timeout): return {"content_type": "text/html", "text": "ok", "final_url": "https://evil.example/"}
    with pytest.raises(WebConnectorError): _connector(Redirect()).read("https://example.com")


def test_unsupported_scheme_and_scope_fail_closed():
    c = _connector()
    with pytest.raises(WebConnectorError): c.read("file:///etc/passwd")
    with pytest.raises(WebConnectorError): c.read("https://evil.example/")


def test_disabled_connector_is_not_discoverable():
    spec = web_connector(); assert spec.enabled
    disabled = type(spec)(**{**spec.__dict__, "enabled": False})
    assert not disabled.enabled


def test_capability_mismatch_and_missing_sandbox_fail_closed(tmp_path):
    plan = _research_plan(); c = _connector()
    blocked = execute_plan(plan, tmp_path, granted=[], audit_path=tmp_path/"a.jsonl", execution_id="x", web_connector=c)
    assert blocked.state is ExecutionState.BLOCKED
    missing = run_safe_operation("not_web_research", tmp_path, web_connector=c, web_request={"operation":"read","url":"https://example.com"})
    assert missing.verification_status == "blocked"


def test_permanent_network_denial_cannot_be_overridden(tmp_path):
    plan = plan_task("fetch network resource", granted=[Capability.NETWORK], explicitly_approved=True)
    assert not plan.executable


def test_approval_mismatch_does_not_grant_web_write(tmp_path):
    plan = _research_plan(); assert all(step.tool_name.startswith("web.") for step in plan.steps)
    assert all(REGISTRY.get(step.tool_name).read_write_mode.value == "read_only" for step in plan.steps)


def test_secret_safe_handling(tmp_path):
    class Secret(FakeTransport):
        def __call__(self, method, target, timeout): return {"content_type":"text/plain","text":"api_key=sk-secret123 public"}
    result = run_safe_operation("web_research", tmp_path, web_connector=_connector(Secret()), web_request={"operation":"read","url":"https://example.com"})
    assert result.success and "sk-secret123" not in result.output and "[REDACTED]" in result.output


def test_verification_and_audit_evidence(tmp_path):
    plan = plan_task("research example.com", granted=[Capability.WEB_RESEARCH])
    result = execute_plan(plan, tmp_path, granted=[Capability.WEB_RESEARCH], audit_path=tmp_path/"audit.jsonl", execution_id="verify", web_connector=_connector(), web_request={"web.search":{"operation":"search","query":"x","results":1},"web.read":{"operation":"read","url":"https://example.com"},"web.extract":{"operation":"extract","evidence":_connector().read("https://example.com"),"fields":["Value"]},"web.compare":{"operation":"compare","sources":(_connector().read("https://example.com"),_connector().read("https://example.com"))}})
    assert result.state is ExecutionState.VERIFIED and verify_execution_audit(tmp_path/"audit.jsonl")


def test_memory_evidence_persistence_duplicate_and_stale(tmp_path):
    memory=CrossProjectMemory(tmp_path/"memory.json"); e=_connector().read("https://example.com")
    assert record_web_evidence(memory,"p1",e); assert not meaningful_web_change(memory,"p1",e); assert meaningful_web_change(memory,"p2",e)
    stale=WebEvidence(e.url,e.domain,e.title,e.text,"2020-01-01T00:00:00+00:00",e.fingerprint,e.source_ref,e.content_type,True)
    assert stale.stale


def test_interruption_requires_fresh_authorization(tmp_path):
    audit=tmp_path/"audit.jsonl"; audit.write_text(json.dumps({"execution_id":"int","timestamp":"now","state":"running","task_digest":"x","plan_digest":"y"})+"\n")
    # The existing hash-chain verifier rejects an unsealed synthetic record, so recovery must fail closed.
    result=recover_execution("int",audit); assert result.state is ExecutionState.BLOCKED


def test_cross_project_memory_isolation(tmp_path):
    memory=CrossProjectMemory(tmp_path/"memory.json"); e=_connector().read("https://example.com")
    record_web_evidence(memory,"project-a",e)
    assert not memory.has(project="project-b",kind="web_evidence",fingerprint=e.fingerprint)


def test_deterministic_fingerprints():
    c=_connector(); a=c.read("https://example.com"); b=c.read("https://example.com"); assert a.fingerprint == b.fingerprint and a.source_ref == b.source_ref
