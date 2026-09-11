from __future__ import annotations
import json
from dataclasses import replace
import pytest
from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import ConnectorRegistry, web_connector
from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan, recover_execution
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.task_orchestrator import StructuredTask, TaskOrchestrator
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import REGISTRY
from autonomous_agent.web_domain_connector import WebEvidence, WebResearchConnector, WebConnectorError, meaningful_web_change, record_web_evidence
class FakeTransport:
    def __init__(self): self.calls=[]; self.failures=0
    def __call__(self,method,target,timeout):
        self.calls.append((method,target,timeout))
        if self.failures:self.failures-=1; raise TimeoutError("controlled timeout")
        if method=="SEARCH":return {"results":[{"url":"https://example.com/a","title":"A","snippet":"Value: one"},{"url":"https://example.com/a","title":"duplicate","snippet":"ignored"}]}
        return {"content_type":"text/html","title":"Example","text":"<html><body>Value: one. public content.</body></html>","final_url":target}
def _connector(transport=None):return WebResearchConnector(transport or FakeTransport(),allowed_domains={"example.com"},max_retries=2,timeout_seconds=3)
def _research_plan():return plan_task("research public information from example.com",granted=[Capability.WEB_RESEARCH])
def test_orchestrator_to_safe_executor_to_sandbox(tmp_path):
    connector=_connector(); report=TaskOrchestrator(connector_registry=web_connector()).orchestrate(StructuredTask("research example.com","project-a"),granted=[Capability.WEB_RESEARCH]); assert report.state.value=="authorized"
    plan=_research_plan(); req={"web.search":{"operation":"search","query":"example","results":1},"web.read":{"operation":"read","url":"https://example.com/a"},"web.extract":{"operation":"extract","evidence":connector.read("https://example.com/a"),"fields":["Value"]},"web.compare":{"operation":"compare","sources":(connector.read("https://example.com/a"),connector.read("https://example.com/a"))}}
    result=execute_plan(plan,tmp_path,granted=[Capability.WEB_RESEARCH],audit_path=tmp_path/"audit.jsonl",execution_id="exec-1",web_connector=connector,web_request=req); assert result.state is ExecutionState.VERIFIED and verify_execution_audit(tmp_path/"audit.jsonl")
def test_search_is_bounded_and_deduplicated():
    c=_connector(); assert len(c.search("x",results=10))==1
    with pytest.raises(WebConnectorError):c.search("x"*501)
def test_read_is_bounded_and_verified():
    e=_connector().read("https://example.com/a"); assert e.domain=="example.com" and e.fingerprint and not e.stale
def test_extract_is_deterministic_and_source_linked():
    c=_connector();e=c.read("https://example.com/a");o=c.extract(e,["Value"]);assert o["Value"]["status"]=="verified" and o["Value"]["source_ref"]==e.source_ref
def test_compare_detects_conflict_without_invention():
    c=_connector();a=WebEvidence("https://example.com/a","example.com","a","Value: one.","2026-01-01T00:00:00+00:00","a","a");b=WebEvidence("https://example.com/b","example.com","b","Value: two.","2026-01-01T00:00:00+00:00","b","b");assert any(x["status"]=="conflicting" for x in c.compare((a,b))["facts"])
def test_bounded_response_is_enforced(tmp_path):
    class Big(FakeTransport):
        def __call__(self,method,target,timeout):return {"content_type":"text/plain","text":"x"*2000000,"content_length":2000000}
    r=run_safe_operation("web_research",tmp_path,web_connector=_connector(Big()),web_request={"operation":"read","url":"https://example.com"},output_limit=1024);assert not r.success
def test_bounded_retries(tmp_path):
    t=FakeTransport();t.failures=2;c=_connector(t);r=run_safe_operation("web_research",tmp_path,web_connector=c,web_request={"operation":"read","url":"https://example.com"});assert r.success and len(t.calls)==3
def test_timeout_failure_is_fail_closed(tmp_path):
    class Slow(FakeTransport):
        def __call__(self,method,target,timeout):raise TimeoutError("timeout")
    assert not run_safe_operation("web_research",tmp_path,web_connector=_connector(Slow()),web_request={"operation":"read","url":"https://example.com"}).success
def test_unsafe_redirect_is_rejected():
    class Redirect(FakeTransport):
        def __call__(self,method,target,timeout):return {"content_type":"text/html","text":"ok","final_url":"https://evil.example/"}
    with pytest.raises(WebConnectorError):_connector(Redirect()).read("https://example.com")
def test_unsupported_scheme_and_scope_fail_closed():
    c=_connector()
    with pytest.raises(WebConnectorError):c.read("file:///etc/passwd")
    with pytest.raises(WebConnectorError):c.read("https://evil.example/")
def test_disabled_connector_is_not_discoverable():
    registry=web_connector();spec=registry.get("web_research");disabled=ConnectorRegistry((replace(spec,enabled=False),));assert disabled.resolve_tool("web_research","web.search") is None
def test_capability_mismatch_and_missing_sandbox_fail_closed(tmp_path):
    c=_connector();blocked=execute_plan(_research_plan(),tmp_path,granted=[],audit_path=tmp_path/"a.jsonl",execution_id="x",web_connector=c);assert blocked.state is ExecutionState.BLOCKED
    assert run_safe_operation("not_web_research",tmp_path,web_connector=c,web_request={"operation":"read","url":"https://example.com"}).verification_status=="blocked"
def test_permanent_network_denial_cannot_be_overridden():
    assert not plan_task("fetch network resource",granted=[Capability.NETWORK],explicitly_approved=True).executable
def test_approval_mismatch_does_not_grant_web_write():
    assert all(REGISTRY.get(x.tool_name).read_write_mode.value=="read_only" for x in _research_plan().steps)
def test_secret_safe_handling(tmp_path):
    class Secret(FakeTransport):
        def __call__(self,method,target,timeout):return {"content_type":"text/plain","text":"api_key=sk-secret123 public"}
    r=run_safe_operation("web_research",tmp_path,web_connector=_connector(Secret()),web_request={"operation":"read","url":"https://example.com"});assert r.success and "sk-secret123" not in r.output and "[REDACTED]" in r.output
def test_verification_and_audit_evidence(tmp_path):
    c=_connector();req={"web.search":{"operation":"search","query":"x","results":1},"web.read":{"operation":"read","url":"https://example.com"},"web.extract":{"operation":"extract","evidence":c.read("https://example.com"),"fields":["Value"]},"web.compare":{"operation":"compare","sources":(c.read("https://example.com"),c.read("https://example.com"))}};r=execute_plan(_research_plan(),tmp_path,granted=[Capability.WEB_RESEARCH],audit_path=tmp_path/"audit.jsonl",execution_id="verify",web_connector=c,web_request=req);assert r.state is ExecutionState.VERIFIED and verify_execution_audit(tmp_path/"audit.jsonl")
def test_memory_evidence_persistence_duplicate_and_stale(tmp_path):
    m=CrossProjectMemory(tmp_path/"memory.json");e=_connector().read("https://example.com");assert record_web_evidence(m,"p1",e);assert not meaningful_web_change(m,"p1",e);assert meaningful_web_change(m,"p2",e)
def test_interruption_fails_closed(tmp_path):
    audit=tmp_path/"audit.jsonl";audit.write_text(json.dumps({"execution_id":"int","state":"running"})+"\n");assert recover_execution("int",audit).state is ExecutionState.BLOCKED
def test_cross_project_memory_isolation(tmp_path):
    m=CrossProjectMemory(tmp_path/"memory.json");e=_connector().read("https://example.com");record_web_evidence(m,"project-a",e);assert not m.has(project="project-b",kind="web_evidence",fingerprint=e.fingerprint)
def test_deterministic_fingerprints():
    c=_connector();a=c.read("https://example.com");b=c.read("https://example.com");assert a.fingerprint==b.fingerprint and a.source_ref==b.source_ref
