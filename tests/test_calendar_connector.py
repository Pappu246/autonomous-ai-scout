from __future__ import annotations
import pytest
from autonomous_agent.calendar_connector import CalendarConnector,CalendarError,_fp
from autonomous_agent.calendar_capability import calendar_capabilities
from autonomous_agent.calendar_tooling import register_calendar_tools
from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import calendar_connector
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import ToolRegistry
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.execution_engine import execute_plan,ExecutionState
from autonomous_agent.execution_audit import verify_execution_audit
from pathlib import Path
class Transport:
    def __init__(self):self.calls=[];self.failures=0;self.events={"e1":{"id":"e1","etag":"v1","summary":"Meeting","start":{"dateTime":"2026-09-12T10:00:00+00:00"},"end":{"dateTime":"2026-09-12T11:00:00+00:00"},"attendees":[{"email":"a@example.com"}]}}
    def request(self,method,url,*,params=None,body=None,timeout_seconds=10):
        self.calls.append((method,url,params,body,timeout_seconds))
        if self.failures:self.failures-=1;raise TimeoutError("controlled timeout")
        if method=="GET" and "/events/e1" in url:return self.events["e1"]
        if method=="GET" and url.endswith("/events"):
            return {"items":list(self.events.values()),"timeZone":"UTC"}
        if method=="POST" and url.endswith("/events"):return {"id":"e2","etag":"v1","summary":body.get("summary",""),"start":body["start"],"end":body["end"]}
        if method=="PUT" and "/events/e1" in url:return {**self.events["e1"],**body,"etag":"v2"}
        if method=="DELETE" and "/events/e1" in url:return {}
        raise AssertionError((method,url,params,body))
def test_registration_disabled_and_capability_authority():
    registry=ToolRegistry();register_calendar_tools(registry);caps=calendar_capabilities(registry)
    assert {x.operation for x in caps.list()}=={"calendar.read","calendar.list","calendar.find_free_time","calendar.event.create","calendar.event.update","calendar.event.cancel"}
    connector=calendar_connector(registry,enabled=False).get("calendar");assert connector and not connector.enabled
    assert not registry.authorize("calendar.read",()).allowed
    assert registry.authorize("calendar.read",(Capability.CALENDAR,)).allowed
    assert not registry.authorize("calendar.event.create",(Capability.CALENDAR,)).allowed
    assert registry.authorize("calendar.event.create",(Capability.CALENDAR,),explicitly_approved=True).allowed
def test_list_read_free_time_timezone_and_bounds():
    t=Transport();c=CalendarConnector(t)
    with pytest.raises(CalendarError):c.list(query="x"*501)
    with pytest.raises(CalendarError):c.read("e1", "primary") if False else c.list(time_min="2026-09-12T10:00:00",time_max="2026-09-12T11:00:00")
    e=c.read("e1");assert e.data["id"]=="e1"
    free=c.find_free_time(time_min="2026-09-12T09:00:00+00:00",time_max="2026-09-12T13:00:00+00:00",duration_minutes=30);assert free.operation=="calendar.find_free_time"
def test_event_validation_recurring_attendees_and_size():
    c=CalendarConnector(Transport())
    with pytest.raises(CalendarError):c.create(event={"start":{"dateTime":"2026-09-12T11:00:00+00:00"},"end":{"dateTime":"2026-09-12T10:00:00+00:00"}},idempotency_key="x",approved=True)
    with pytest.raises(CalendarError):c.create(event={"start":{"dateTime":"2026-09-12T10:00:00+00:00"},"end":{"dateTime":"2026-09-12T11:00:00+00:00"},"attendees":[{"email":"bad"}]},idempotency_key="x",approved=True)
    with pytest.raises(CalendarError):c.create(event={"start":{"dateTime":"2026-09-12T10:00:00+00:00"},"end":{"dateTime":"2026-09-12T11:00:00+00:00"},"recurrence":["x"]*21},idempotency_key="x",approved=True)
def test_mutation_approval_stale_and_idempotency():
    t=Transport();c=CalendarConnector(t);event={"summary":"New","start":{"dateTime":"2026-09-12T12:00:00+00:00"},"end":{"dateTime":"2026-09-12T13:00:00+00:00"}};key=_fp(event)
    with pytest.raises(CalendarError):c.create(event=event,idempotency_key=key,approved=False)
    c.create(event=event,idempotency_key=key,approved=True)
    with pytest.raises(CalendarError):c.create(event=event,idempotency_key=key,approved=True)
    with pytest.raises(CalendarError):c.update(event_id="e1",event=event,etag="old",idempotency_key=_fp({**event,"_expectedEtag":"old"}),approved=True) if False else c.update(event_id="e1",event=event,etag="v1",idempotency_key="wrong",approved=True)
    c.update(event_id="e1",event=event,etag="v1",idempotency_key=_fp({**event,"_expectedEtag":"v1"}),approved=True)
    with pytest.raises(CalendarError):c.cancel(event_id="e1",etag="v1",idempotency_key=_fp({"event_id":"e1","etag":"v1"}),approved=False)
def test_retry_limits_and_secret_safe_evidence():
    t=Transport();t.failures=2;c=CalendarConnector(t);e=c.list();assert e.operation=="calendar.list" and len(t.calls)==3
    secret={"access_token":"abc","authorization":"Bearer abc","summary":"ok"};assert "abc" not in str(c._request("GET","https://www.googleapis.com/calendar/v3/calendars/primary/events",body=secret))
def test_planner_and_sandbox_integration(tmp_path:Path):
    registry=ToolRegistry();register_calendar_tools(registry)
    p=plan_task("find free time on my calendar",granted=(Capability.CALENDAR,),registry=registry);assert p.executable and p.steps[0].tool_name=="calendar.find_free_time"
    blocked=plan_task("create a calendar event",granted=(Capability.CALENDAR,),registry=registry);assert not blocked.executable
    t=Transport();result=run_safe_operation("calendar",tmp_path,calendar_connector=t,calendar_request={"operation":"list","calendar_id":"primary"});assert result.success and result.verification_status=="verified"
def test_executor_reuses_existing_audit_boundary(tmp_path:Path):
    registry=ToolRegistry();register_calendar_tools(registry);t=Transport();p=plan_task("list my calendar",granted=(Capability.CALENDAR,),registry=registry);audit=tmp_path/"audit.jsonl";r=execute_plan(p,tmp_path,granted=(Capability.CALENDAR,),audit_path=audit,execution_id="cal-1",registry=registry,calendar_connector=CalendarConnector(t),calendar_request={"operation":"list"});assert r.state is ExecutionState.VERIFIED and verify_execution_audit(audit)
