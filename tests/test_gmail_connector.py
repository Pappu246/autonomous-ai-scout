from __future__ import annotations
import json
import pytest
from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import gmail_connector
from autonomous_agent.gmail_capability import gmail_capabilities
from autonomous_agent.gmail_connector import GmailConnector,GmailError,_parse_mime,_fingerprint
from autonomous_agent.gmail_tooling import register_gmail_tools
from autonomous_agent.task_plan_models import TaskIntent
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import ToolRegistry
class Transport:
    def __init__(self): self.calls=[]; self.failures=0
    def request(self,method,url,*,params=None,body=None,timeout_seconds=10):
        self.calls.append((method,url,params,body,timeout_seconds))
        if self.failures: self.failures-=1; raise TimeoutError("controlled timeout")
        if url.endswith("/messages") and method=="GET": return {"messages":[{"id":"m1","threadId":"t1"},{"id":"m2","threadId":"t2"}],"resultSizeEstimate":2}
        if "/messages/m1" in url:return {"id":"m1","threadId":"t1","labelIds":["INBOX"],"snippet":"hello","payload":{"headers":[{"name":"Subject","value":"Hello"}]}}
        if "/threads/t1" in url:return {"id":"t1","messages":[{"id":"m2","threadId":"t1","internalDate":"2","snippet":"later"},{"id":"m1","threadId":"t1","internalDate":"1","snippet":"first"}]}
        if url.endswith("/drafts") and method=="POST":return {"id":"d1","message":{"id":"m-draft","threadId":"t1"}}
        if url.endswith("/messages/send") and method=="POST":return {"id":"sent-1","threadId":"t1"}
        raise AssertionError((method,url,params,body))
def test_capability_registration_and_disabled_connector():
    registry=ToolRegistry();register_gmail_tools(registry);caps=gmail_capabilities(registry)
    assert {x.operation for x in caps.list()}=={"email.search","email.read","email.thread","email.draft","email.send"}
    connector=gmail_connector(registry,enabled=False).get("gmail");assert connector and connector.enabled is False
def test_scope_and_authorization_fail_closed():
    registry=ToolRegistry();register_gmail_tools(registry)
    assert not registry.authorize("email.read",(),sandbox_available=True,audit_available=True).allowed
    assert registry.authorize("email.read",(Capability.EMAIL,),sandbox_available=True,audit_available=True).allowed
    assert not registry.authorize("email.send",(Capability.EMAIL,),sandbox_available=True,audit_available=True).allowed
    assert registry.authorize("email.send",(Capability.EMAIL,),explicitly_approved=True,sandbox_available=True,audit_available=True).allowed
def test_malformed_query_and_bounds():
    t=Transport();c=GmailConnector(t)
    with pytest.raises(GmailError):c.search("x"*501)
    e=c.search("in:inbox",results=999);assert len(e.data["messages"])==2 and t.calls[-1][2]["maxResults"]==20
def test_message_and_thread_bounds_and_determinism():
    t=Transport();c=GmailConnector(t);assert c.read("m1").data["id"]=="m1";one=c.thread("t1");two=c.thread("t1");assert one.fingerprint==two.fingerprint and [m["id"] for m in one.data["messages"]]==["m1","m2"]
def test_mime_parsing_and_attachment_limit():
    raw=(b"From: a@example.com\nTo: b@example.com\nSubject: Hi\nContent-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: text/plain\n\nhello access_token=secret123\n--x\nContent-Type: application/octet-stream\nContent-Disposition: attachment; filename=a.bin\n\n123\n--x--\n");parsed=_parse_mime(raw);assert parsed["subject"]=="Hi" and parsed["attachments"][0]["filename"]=="a.bin" and "secret123" not in json.dumps(parsed)
def test_secret_redaction_and_credential_reference_only():
    t=Transport();c=GmailConnector(t,credential_reference="gmail:oauth:user");assert c.credential_reference=="gmail:oauth:user"
    with pytest.raises(GmailError):GmailConnector(t,credential_reference="access_token=abc")
def test_draft_never_sends():
    t=Transport();c=GmailConnector(t);e=c.draft(to="a@example.com",subject="Hi",body="hello");assert e.operation=="email.draft" and not any(url.endswith("/messages/send") for _,url,_,_,_ in t.calls)
def test_send_requires_approval_and_idempotency():
    t=Transport();c=GmailConnector(t);key=_fingerprint({"to":"a@example.com","subject":"Hi","body":"hello","threadId":""})
    with pytest.raises(GmailError):c.send(to="a@example.com",subject="Hi",body="hello",idempotency_key=key,approved=False)
    e=c.send(to="a@example.com",subject="Hi",body="hello",idempotency_key=key,approved=True);assert e.operation=="email.send" and any(url.endswith("/messages/send") for _,url,_,_,_ in t.calls)
def test_timeout_retry_is_bounded():
    t=Transport();t.failures=2;c=GmailConnector(t);e=c.search("hello");assert e.operation=="email.search" and len(t.calls)==3
def test_planner_email_integration_and_draft_gate():
    registry=ToolRegistry();register_gmail_tools(registry);read_plan=plan_task("search my Gmail for invoices",granted=(Capability.EMAIL,),registry=registry);assert read_plan.intent is TaskIntent.EMAIL and {s.tool_name for s in read_plan.steps}=={"email.search","email.read","email.thread"};draft_plan=plan_task("draft an email to finance",granted=(Capability.EMAIL,),registry=registry);assert [s.tool_name for s in draft_plan.steps]==["email.draft"] and not draft_plan.executable
def test_orchestrator_boundary_can_use_registered_email_tools():
    registry=ToolRegistry();register_gmail_tools(registry);connector=gmail_connector(registry,enabled=False);assert connector.resolve_tool("gmail","email.read") is None and connector.get("gmail").registered_tools[-1]=="email.send"
def test_cross_project_identity_is_deterministic():
    payload={"project":"a","message":"m1","thread":"t1"};assert _fingerprint(payload)==_fingerprint(dict(reversed(list(payload.items()))))
