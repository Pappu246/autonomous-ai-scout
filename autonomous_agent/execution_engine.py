from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from datetime import datetime,timezone
from enum import Enum
from pathlib import Path
from typing import Any,Iterable,Mapping
from .capability_policy import Capability
from .cross_project_memory import CrossProjectMemory
from .execution_audit import append_execution_record,verify_execution_audit
from .sandbox import MAX_OUTPUT_BYTES,MAX_TIMEOUT_SECONDS,SandboxResult,run_safe_operation
from .task_plan_models import TaskPlan
from .tool_registry import REGISTRY,ToolRegistry
class ExecutionState(str,Enum):BLOCKED="blocked";RUNNING="running";VERIFIED="verified";FAILED="failed";RECOVERY_REQUIRED="recovery_required"
@dataclass(frozen=True)
class ExecutionResult:state:ExecutionState;reason:str;attempts:int;results:tuple[SandboxResult,...];audit_path:str
_CAPABILITY_TO_OPERATION={Capability.INSPECT:"inspect",Capability.TEST:"test",Capability.LINT:"lint",Capability.METRICS:"metrics",Capability.READ_FILE:"read_file",Capability.BENCHMARK:"benchmark",Capability.WEB_RESEARCH:"web_research",Capability.FILES_WORKSPACE:"filesystem_workspace",Capability.EMAIL:"gmail"}
MAX_RETRIES=2
def _now():return datetime.now(timezone.utc).isoformat()
def _audit(path,execution_id,state,**extra):append_execution_record(path,{"execution_id":execution_id,"timestamp":_now(),"state":state.value,**{k:str(v) for k,v in extra.items()}})
def _remember(memory,project,*,task=None,tool=None,execution_id="",outcome="",attempts=0):
    if memory is None:return
    try:
        if task is not None:memory.record_task(project,task,intent="execution",outcome=outcome)
        if tool is not None:memory.record_tool_execution(project,tool,execution_id=execution_id,outcome=outcome,attempts=attempts)
    except Exception:return
def _has_unfinished_execution(path,execution_id):
    if not path.exists():return False
    terminal={ExecutionState.VERIFIED.value,ExecutionState.FAILED.value,ExecutionState.BLOCKED.value};last=None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():continue
        item=json.loads(line)
        if isinstance(item,dict) and item.get("execution_id")==execution_id:last=str(item.get("state",""))
    return last==ExecutionState.RUNNING.value or (last is not None and last not in terminal)
def recover_execution(execution_id,audit_path):
    if not verify_execution_audit(audit_path):return ExecutionResult(ExecutionState.BLOCKED,"execution audit chain is invalid",0,(),str(audit_path))
    if _has_unfinished_execution(audit_path,execution_id):return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"interrupted execution requires fresh authorization; automatic replay is disabled",0,(),str(audit_path))
    return ExecutionResult(ExecutionState.VERIFIED,"no unfinished execution requires recovery",0,(),str(audit_path))
def _validate_web_tool(tool):
    expected="required" if tool.name in {"web.search","web.read"} else "none";return tool.capability==Capability.WEB_RESEARCH.value and tool.network_requirement.value==expected and tool.authentication_requirement.value=="none" and tool.read_write_mode.value=="read_only" and tool.approval_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"web.search","web.read","web.extract","web.compare"}
def _validate_files_tool(tool):
    return tool.capability in {Capability.READ_FILE.value,Capability.FILES_WORKSPACE.value} and tool.network_requirement.value=="none" and tool.authentication_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"filesystem.read","filesystem.list","filesystem.write","filesystem.transform"}
def _validate_email_tool(tool):
    return tool.capability==Capability.EMAIL.value and tool.network_requirement.value=="required" and tool.authentication_requirement.value=="user_auth" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"email.search","email.read","email.thread","email.draft","email.send"}
def execute_plan(plan:TaskPlan,root:Path,*,granted:Iterable[Capability|str]=(),explicitly_approved=False,sandbox_available=True,audit_path:Path,execution_id:str,registry:ToolRegistry=REGISTRY,max_retries=0,timeout_seconds=30,output_limit=MAX_OUTPUT_BYTES,memory:CrossProjectMemory|None=None,project="local",web_connector:Any=None,web_request:Mapping[str,Any]|None=None,workspace_connector:Any=None,workspace_request:Mapping[str,Any]|None=None,gmail_connector:Any=None,gmail_request:Mapping[str,Any]|None=None)->ExecutionResult:
    if not execution_id.strip():return ExecutionResult(ExecutionState.BLOCKED,"execution identity is required",0,(),str(audit_path))
    if not plan.executable:return ExecutionResult(ExecutionState.BLOCKED,"task plan is not executable",0,(),str(audit_path))
    if not sandbox_available:return ExecutionResult(ExecutionState.BLOCKED,"sandbox is unavailable",0,(),str(audit_path))
    if not verify_execution_audit(audit_path):return ExecutionResult(ExecutionState.BLOCKED,"execution audit chain is invalid",0,(),str(audit_path))
    if _has_unfinished_execution(audit_path,execution_id):return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"execution was interrupted; fresh authorization is required",0,(),str(audit_path))
    retries=max(0,min(int(max_retries),MAX_RETRIES));timeout=max(1,min(int(timeout_seconds),MAX_TIMEOUT_SECONDS));output=max(1,min(int(output_limit),MAX_OUTPUT_BYTES));_audit(audit_path,execution_id,ExecutionState.RUNNING,task_digest=hashlib.sha256(plan.task.encode()).hexdigest(),plan_digest=plan.audit.plan_digest);_remember(memory,project,task=plan.task,execution_id=execution_id,outcome="started")
    results=[];total_attempts=0
    for step in plan.steps:
        tool=registry.get(step.tool_name)
        if tool is None:_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="unknown tool",tool=step.tool_name);return ExecutionResult(ExecutionState.BLOCKED,f"unknown tool is blocked: {step.tool_name}",total_attempts,tuple(results),str(audit_path))
        decision=registry.authorize(tool.name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=True)
        if not decision.allowed:_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason=decision.reason,tool=tool.name);return ExecutionResult(ExecutionState.BLOCKED,f"authorization blocked for {tool.name}: {decision.reason}",total_attempts,tuple(results),str(audit_path))
        try:capability=Capability(tool.capability);operation=_CAPABILITY_TO_OPERATION[capability]
        except (ValueError,KeyError):_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="capability has no safe sandbox operation",tool=tool.name);return ExecutionResult(ExecutionState.BLOCKED,f"no safe sandbox operation exists for {tool.name}",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.WEB_RESEARCH and not _validate_web_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"web tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE} and not _validate_files_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"filesystem tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.EMAIL and not _validate_email_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"email tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if tool.read_write_mode.value!="read_only" and not (capability in {Capability.FILES_WORKSPACE,Capability.EMAIL} and explicitly_approved):return ExecutionResult(ExecutionState.BLOCKED,"write operation requires explicit approval",total_attempts,tuple(results),str(audit_path))
        if not tool.safe_autonomous and not (capability in {Capability.FILES_WORKSPACE,Capability.EMAIL} and explicitly_approved):return ExecutionResult(ExecutionState.BLOCKED,f"tool is outside the safe autonomous execution boundary: {tool.name}",total_attempts,tuple(results),str(audit_path))
        for attempt in range(retries+1):
            total_attempts+=1;request=None;connector=None
            if capability is Capability.WEB_RESEARCH and isinstance(web_request,Mapping):candidate=web_request.get(tool.name,web_request);request=candidate if isinstance(candidate,Mapping) else None;connector=web_connector
            if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE} and isinstance(workspace_request,Mapping):candidate=workspace_request.get(tool.name,workspace_request);request=candidate if isinstance(candidate,Mapping) else None;connector=workspace_connector
            if capability is Capability.EMAIL and isinstance(gmail_request,Mapping):candidate=gmail_request.get(tool.name,gmail_request);request=dict(candidate) if isinstance(candidate,Mapping) else None;connector=gmail_connector
            if capability is Capability.EMAIL and tool.name=="email.send" and isinstance(request,dict):request["approved"]=bool(explicitly_approved)
            result=run_safe_operation(operation,root,timeout_seconds=timeout,output_limit=output,web_connector=connector if capability is Capability.WEB_RESEARCH else None,web_request=request if capability is Capability.WEB_RESEARCH else None,workspace_connector=connector if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE} else None,workspace_request=request if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE} else None,gmail_connector=connector if capability is Capability.EMAIL else None,gmail_request=request if capability is Capability.EMAIL else None);results.append(result);_audit(audit_path,execution_id,ExecutionState.RUNNING,tool=tool.name,attempt=attempt+1,result="success" if result.success else "failure",verification=result.verification_status)
            if result.success and result.verification_status=="verified":break
        else:_audit(audit_path,execution_id,ExecutionState.FAILED,tool=tool.name,reason="bounded retries exhausted");return ExecutionResult(ExecutionState.FAILED,f"tool execution failed after bounded retries: {tool.name}",total_attempts,tuple(results),str(audit_path))
        _remember(memory,project,tool=tool.name,execution_id=execution_id,outcome="verified",attempts=total_attempts)
    if not results or any(not r.success or r.verification_status!="verified" for r in results):return ExecutionResult(ExecutionState.FAILED,"post-action verification failed",total_attempts,tuple(results),str(audit_path))
    _audit(audit_path,execution_id,ExecutionState.VERIFIED,attempts=total_attempts);_remember(memory,project,execution_id=execution_id,outcome="verified",attempts=total_attempts);return ExecutionResult(ExecutionState.VERIFIED,"all planned actions executed and verified through the existing sandbox",total_attempts,tuple(results),str(audit_path))
