from __future__ import annotations
from pathlib import Path
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState,execute_plan
from autonomous_agent.gmail_connector import GmailConnector
from autonomous_agent.gmail_tooling import register_gmail_tools
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import ToolRegistry
class Transport:
    def request(self,method,url,*,params=None,body=None,timeout_seconds=10):
        if url.endswith("/messages"):return {"messages":[{"id":"m1","threadId":"t1"}],"resultSizeEstimate":1}
        raise AssertionError(url)
def test_gmail_read_flows_through_existing_executor(tmp_path: Path):
    registry=ToolRegistry();register_gmail_tools(registry)
    plan=plan_task("search Gmail for invoices",granted=(Capability.EMAIL,),registry=registry)
    # The plan is read-only, but its three operations require a connector request for each step.
    request={"email.search":{"operation":"search","query":"in:inbox","results":1},"email.read":{"operation":"read","message_id":"m1"},"email.thread":{"operation":"thread","thread_id":"t1"}}
    audit=tmp_path/"audit.jsonl";audit.write_text("",encoding="utf-8")
    result=execute_plan(plan,tmp_path,granted=(Capability.EMAIL,),audit_path=audit,execution_id="gmail-read-1",registry=registry,gmail_connector=GmailConnector(Transport()),gmail_request=request)
    assert result.state is ExecutionState.VERIFIED
    assert all(x.verification_status=="verified" for x in result.results)
    assert "gmail-read-1" in audit.read_text(encoding="utf-8")
