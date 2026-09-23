from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from datetime import datetime,timezone
from enum import Enum
from pathlib import Path
from typing import Any,Iterable,Mapping
from .capability_policy import Capability
from .consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy
from .prompt_injection_guard import TrustLevel
from .cross_project_memory import CrossProjectMemory
from .execution_checkpoint import ExecutionCheckpointStore
from .execution_audit import append_execution_record,verify_execution_audit
from .sandbox import MAX_OUTPUT_BYTES,MAX_TIMEOUT_SECONDS,SandboxResult,run_safe_operation
from .task_plan_models import TaskPlan
from .tool_registry import REGISTRY,ToolRegistry
class ExecutionState(str,Enum):BLOCKED="blocked";RUNNING="running";VERIFIED="verified";FAILED="failed";RECOVERY_REQUIRED="recovery_required"
@dataclass(frozen=True)
class ExecutionResult:state:ExecutionState;reason:str;attempts:int;results:tuple[SandboxResult,...];audit_path:str
_CAPABILITY_TO_OPERATION={Capability.INSPECT:"inspect",Capability.TEST:"test",Capability.LINT:"lint",Capability.METRICS:"metrics",Capability.READ_FILE:"read_file",Capability.BENCHMARK:"benchmark",Capability.WEB_RESEARCH:"web_research",Capability.REST_API:"rest",Capability.FILES_WORKSPACE:"filesystem_workspace",Capability.WORKSPACE_SHELL:"workspace_shell",Capability.EMAIL:"gmail",Capability.CALENDAR:"calendar",Capability.BROWSER:"browser"}
MAX_RETRIES=2
def _now():return datetime.now(timezone.utc).isoformat()
def _audit(path,execution_id,state,**extra):append_execution_record(path,{"execution_id":execution_id,"timestamp":_now(),"state":state.value,**{k:str(v) for k,v in extra.items()}})
def _authorization_digest(granted, explicitly_approved, plan=None, registry=REGISTRY):
    values=sorted({str(item.value if isinstance(item,Capability) else item).strip().lower() for item in granted})
    tools=[]
    if plan is not None:
        for step in plan.steps:
            tool=registry.get(step.tool_name)
            if tool is None:
                tools.append({"name":step.tool_name,"missing":True})
            else:
                tools.append({
                    "name":tool.name,
                    "capability":tool.capability,
                    "risk":tool.risk_level.value,
                    "read_write":tool.read_write_mode.value,
                    "network":tool.network_requirement.value,
                    "authentication":tool.authentication_requirement.value,
                    "approval":tool.approval_requirement.value,
                    "sandbox":tool.sandbox_requirement.value,
                    "audit":tool.audit_requirement.value,
                    "safe_autonomous":tool.safe_autonomous,
                })
    payload={"granted":values,"explicitly_approved":bool(explicitly_approved),"tools":tools}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _audit_events(path,execution_id):
    if not path.exists():return ()
    events=[]
    try:lines=path.read_text(encoding="utf-8").splitlines()
    except OSError:raise ValueError("execution audit is unreadable")
    for line in lines:
        if not line.strip():continue
        item=json.loads(line)
        if isinstance(item,dict) and item.get("execution_id")==execution_id:events.append(item)
    return tuple(events)

def _audit_has_verified_completion(path,execution_id,plan):
    events=_audit_events(path,execution_id)
    verified_steps={str(item.get("step_id")) for item in events if item.get("event")=="tool_result" and item.get("result")=="success" and item.get("verification")=="verified" and item.get("step_id")}
    terminal=any(item.get("event")=="checkpoint_verified" and item.get("state")==ExecutionState.VERIFIED.value for item in events)
    return all(step.step_id in verified_steps for step in plan.steps) and terminal

def _verified_steps_from_audit(path,execution_id):
    completed=set()
    if not path.exists():return completed
    try:lines=path.read_text(encoding="utf-8").splitlines()
    except OSError:return completed
    for line in lines:
        try:item=json.loads(line)
        except (TypeError,ValueError):continue
        if isinstance(item,dict) and item.get("execution_id")==execution_id and item.get("event")=="tool_result" and item.get("result")=="success" and item.get("verification")=="verified" and item.get("step_id"):completed.add(str(item["step_id"]))
    return completed

def _remember(memory,project,*,task=None,tool=None,execution_id="",outcome="",attempts=0):
    if memory is None:return
    try:
        if task is not None:memory.record_task(project,task,intent="execution",outcome=outcome)
        if tool is not None:memory.record_tool_execution(project,tool,execution_id=execution_id,outcome=outcome,attempts=attempts)
    except Exception:return
def _has_unfinished_execution(path,execution_id):
    if not path.exists():return False
    terminal={ExecutionState.VERIFIED.value,ExecutionState.FAILED.value,ExecutionState.BLOCKED.value};last=None
    try:
        lines=path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():continue
            item=json.loads(line)
            if isinstance(item,dict) and item.get("execution_id")==execution_id:last=str(item.get("state",""))
    except (OSError,UnicodeError,json.JSONDecodeError):
        raise ValueError("execution audit contains an invalid record")
    return last==ExecutionState.RUNNING.value or (last is not None and last not in terminal)
def recover_execution(execution_id,audit_path):
    if not verify_execution_audit(audit_path):return ExecutionResult(ExecutionState.BLOCKED,"execution audit chain is invalid",0,(),str(audit_path))
    try:unfinished=_has_unfinished_execution(audit_path,execution_id)
    except ValueError as exc:return ExecutionResult(ExecutionState.BLOCKED,str(exc),0,(),str(audit_path))
    if unfinished:return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"interrupted execution requires fresh authorization; automatic replay is disabled",0,(),str(audit_path))
    return ExecutionResult(ExecutionState.VERIFIED,"no unfinished execution requires recovery",0,(),str(audit_path))
def _validate_web_tool(tool):
    expected="required" if tool.name in {"web.search","web.read"} else "none";return tool.capability==Capability.WEB_RESEARCH.value and tool.network_requirement.value==expected and tool.authentication_requirement.value=="none" and tool.read_write_mode.value=="read_only" and tool.approval_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"web.search","web.read","web.extract","web.compare"}
def _validate_files_tool(tool):
    return tool.capability in {Capability.READ_FILE.value,Capability.FILES_WORKSPACE.value} and tool.network_requirement.value=="none" and tool.authentication_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"filesystem.read","filesystem.list","filesystem.write","filesystem.transform"}
def _validate_workspace_shell_tool(tool):
    return tool.capability==Capability.WORKSPACE_SHELL.value and tool.network_requirement.value=="none" and tool.authentication_requirement.value=="none" and tool.read_write_mode.value=="read_only" and tool.approval_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name=="workspace.shell"
def _validate_email_tool(tool):
    return tool.capability==Capability.EMAIL.value and tool.network_requirement.value=="required" and tool.authentication_requirement.value=="user_auth" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"email.search","email.read","email.thread","email.draft","email.send"}
def _validate_calendar_tool(tool):
    return tool.capability==Capability.CALENDAR.value and tool.network_requirement.value=="required" and tool.authentication_requirement.value=="user_auth" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"calendar.read","calendar.list","calendar.find_free_time","calendar.event.create","calendar.event.update","calendar.event.cancel"}
def _validate_browser_tool(tool):
    return tool.capability==Capability.BROWSER.value and tool.network_requirement.value=="required" and tool.authentication_requirement.value=="none" and tool.read_write_mode.value=="read_only" and tool.approval_requirement.value=="none" and tool.sandbox_requirement.value=="required" and tool.audit_requirement.value=="required" and tool.name in {"browser.open","browser.click","browser.extract"}
def execute_plan(plan:TaskPlan,root:Path,*,granted:Iterable[Capability|str]=(),explicitly_approved=False,origin_trust:TrustLevel=TrustLevel.USER,sandbox_available=True,audit_path:Path,execution_id:str,registry:ToolRegistry=REGISTRY,max_retries=0,timeout_seconds=30,output_limit=MAX_OUTPUT_BYTES,memory:CrossProjectMemory|None=None,project="local",web_connector:Any=None,web_request:Mapping[str,Any]|None=None,rest_connector:Any=None,rest_request:Mapping[str,Any]|None=None,workspace_connector:Any=None,workspace_request:Mapping[str,Any]|None=None,gmail_connector:Any=None,gmail_request:Mapping[str,Any]|None=None,calendar_connector:Any=None,calendar_request:Mapping[str,Any]|None=None,browser_connector:Any=None,browser_request:Mapping[str,Any]|None=None,checkpoint_path:Path|None=None)->ExecutionResult:
    granted=tuple(granted)
    if not execution_id.strip():return ExecutionResult(ExecutionState.BLOCKED,"execution identity is required",0,(),str(audit_path))
    if not plan.executable:return ExecutionResult(ExecutionState.BLOCKED,"task plan is not executable",0,(),str(audit_path))
    if not sandbox_available:return ExecutionResult(ExecutionState.BLOCKED,"sandbox is unavailable",0,(),str(audit_path))
    if not verify_execution_audit(audit_path):return ExecutionResult(ExecutionState.BLOCKED,"execution audit chain is invalid",0,(),str(audit_path))
    checkpoint_store=ExecutionCheckpointStore(checkpoint_path or audit_path.with_suffix(".checkpoint.json"))
    task_digest=hashlib.sha256(plan.task.encode()).hexdigest()
    plan_digest=plan.audit.plan_digest
    authorization_digest=_authorization_digest(granted, explicitly_approved, plan, registry)
    try:checkpoint=checkpoint_store.load()
    except ValueError as exc:return ExecutionResult(ExecutionState.BLOCKED,str(exc),0,(),str(audit_path))
    if checkpoint is not None:
        if checkpoint.execution_id!=execution_id or checkpoint.task_digest!=task_digest or checkpoint.plan_digest!=plan_digest or checkpoint.authorization_digest!=authorization_digest:
            return ExecutionResult(ExecutionState.BLOCKED,"execution checkpoint does not match this task",0,(),str(audit_path))
        if checkpoint.state=="verified":
            try:verified=_audit_has_verified_completion(audit_path,execution_id,plan)
            except (OSError,UnicodeError,json.JSONDecodeError,ValueError) as exc:return ExecutionResult(ExecutionState.BLOCKED,f"execution audit cannot verify checkpoint: {type(exc).__name__}",checkpoint.total_attempts,(),str(audit_path))
            if verified:
                return ExecutionResult(ExecutionState.VERIFIED,"execution already verified by trusted audit and durable checkpoint",checkpoint.total_attempts,(),str(audit_path))
            return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"checkpoint claims verified execution without matching trusted audit evidence",checkpoint.total_attempts,(),str(audit_path))
        if checkpoint.state=="failed":
            return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"failed execution checkpoint requires explicit recovery; automatic replay is disabled",checkpoint.total_attempts,(),str(audit_path))
        if checkpoint.state=="blocked":
            return ExecutionResult(ExecutionState.BLOCKED,"blocked execution checkpoint cannot be resumed",checkpoint.total_attempts,(),str(audit_path))
        if checkpoint.state!="running":
            return ExecutionResult(ExecutionState.BLOCKED,"execution checkpoint has an invalid resumable state",checkpoint.total_attempts,(),str(audit_path))
        audit_completed=_verified_steps_from_audit(audit_path,execution_id)
        checkpoint_completed=set(checkpoint.completed_step_ids)
        if checkpoint_completed - audit_completed:
            return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"checkpoint claims completed steps without matching trusted audit evidence",checkpoint.total_attempts,(),str(audit_path))
        completed_step_ids=audit_completed
        total_attempts=checkpoint.total_attempts
        _audit(audit_path,execution_id,ExecutionState.RUNNING,event="checkpoint_resumed",completed_steps=len(completed_step_ids))
    else:
        if _has_unfinished_execution(audit_path,execution_id):return ExecutionResult(ExecutionState.RECOVERY_REQUIRED,"execution was interrupted; durable checkpoint is unavailable",0,(),str(audit_path))
        completed_step_ids=set()
        total_attempts=0
        _audit(audit_path,execution_id,ExecutionState.RUNNING,task_digest=task_digest,plan_digest=plan_digest,event="execution_started")
    retries=max(0,min(int(max_retries),MAX_RETRIES));timeout=max(1,min(int(timeout_seconds),MAX_TIMEOUT_SECONDS));output=max(1,min(int(output_limit),MAX_OUTPUT_BYTES))
    checkpoint_store.save(execution_id=execution_id,task_digest=task_digest,plan_digest=plan_digest,authorization_digest=authorization_digest,state=ExecutionState.RUNNING.value,completed_step_ids=tuple(s.step_id for s in plan.steps if s.step_id in completed_step_ids),total_attempts=total_attempts)
    _remember(memory,project,task=plan.task,execution_id=execution_id,outcome="resumed" if checkpoint is not None else "started")
    results=[]
    for step in plan.steps:
        if step.step_id in completed_step_ids:
            _audit(audit_path,execution_id,ExecutionState.RUNNING,event="checkpoint_step_skipped",tool=step.tool_name,step_id=step.step_id)
            continue
        tool=registry.get(step.tool_name)
        if tool is None:_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="unknown tool",tool=step.tool_name);return ExecutionResult(ExecutionState.BLOCKED,f"unknown tool is blocked: {step.tool_name}",total_attempts,tuple(results),str(audit_path))
        consequence=ConsequenceAwareApprovalPolicy().evaluate(tool, origin_trust=origin_trust, explicitly_approved=explicitly_approved)
        if consequence.mode is ApprovalMode.REQUIRE_APPROVAL and not explicitly_approved:
            _audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="consequence-aware policy requires explicit approval",tool=tool.name)
            return ExecutionResult(ExecutionState.BLOCKED,f"consequence-aware policy requires explicit approval for {tool.name}",total_attempts,tuple(results),str(audit_path))
        if consequence.mode is ApprovalMode.DENY:
            _audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="consequence-aware policy denies action",tool=tool.name)
            return ExecutionResult(ExecutionState.BLOCKED,f"consequence-aware policy denies {tool.name}",total_attempts,tuple(results),str(audit_path))
        decision=registry.authorize(tool.name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=True)
        if not decision.allowed:_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason=decision.reason,tool=tool.name);return ExecutionResult(ExecutionState.BLOCKED,f"authorization blocked for {tool.name}: {decision.reason}",total_attempts,tuple(results),str(audit_path))
        try:capability=Capability(tool.capability);operation=_CAPABILITY_TO_OPERATION[capability]
        except (ValueError,KeyError):_audit(audit_path,execution_id,ExecutionState.BLOCKED,reason="capability has no safe sandbox operation",tool=tool.name);return ExecutionResult(ExecutionState.BLOCKED,f"no safe sandbox operation exists for {tool.name}",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.WEB_RESEARCH and not _validate_web_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"web tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.WORKSPACE_SHELL and not _validate_workspace_shell_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"workspace shell tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE} and not _validate_files_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"filesystem tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.EMAIL and not _validate_email_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"email tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.CALENDAR and not _validate_calendar_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"calendar tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.REST_API and not (
            tool.name in {"rest.get","rest.head","rest.write"}
            and tool.network_requirement.value=="required"
            and tool.authentication_requirement.value=="user_auth"
            and tool.sandbox_requirement.value=="required"
            and tool.audit_requirement.value=="required"
            and tool.read_write_mode.value in {"read_only","controlled_write"}
            and tool.approval_requirement.value in {"none","human_review"}
        ):
            return ExecutionResult(ExecutionState.BLOCKED,"REST tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if capability is Capability.BROWSER and not _validate_browser_tool(tool):return ExecutionResult(ExecutionState.BLOCKED,"browser tool is incompatible with the safe sandbox boundary",total_attempts,tuple(results),str(audit_path))
        if tool.read_write_mode.value!="read_only" and not (capability in {Capability.FILES_WORKSPACE,Capability.EMAIL,Capability.CALENDAR,Capability.REST_API} and explicitly_approved):return ExecutionResult(ExecutionState.BLOCKED,"write operation requires explicit approval",total_attempts,tuple(results),str(audit_path))
        if not tool.safe_autonomous and not (capability in {Capability.FILES_WORKSPACE,Capability.EMAIL,Capability.CALENDAR,Capability.REST_API} and explicitly_approved):return ExecutionResult(ExecutionState.BLOCKED,f"tool is outside the safe autonomous execution boundary: {tool.name}",total_attempts,tuple(results),str(audit_path))
        for attempt in range(retries+1):
            total_attempts+=1;request=None;connector=None
            if capability is Capability.WEB_RESEARCH and isinstance(web_request,Mapping):candidate=web_request.get(tool.name,web_request);request=candidate if isinstance(candidate,Mapping) else None;connector=web_connector
            if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE,Capability.WORKSPACE_SHELL} and isinstance(workspace_request,Mapping):candidate=workspace_request.get(tool.name,workspace_request);request=candidate if isinstance(candidate,Mapping) else None;connector=workspace_connector
            if capability is Capability.EMAIL and isinstance(gmail_request,Mapping):candidate=gmail_request.get(tool.name,gmail_request);request=dict(candidate) if isinstance(candidate,Mapping) else None;connector=gmail_connector
            if capability is Capability.EMAIL and tool.name=="email.send" and isinstance(request,dict):request["approved"]=bool(explicitly_approved)
            if capability is Capability.CALENDAR and isinstance(calendar_request,Mapping):candidate=calendar_request.get(tool.name,calendar_request);request=dict(candidate) if isinstance(candidate,Mapping) else None;connector=calendar_connector
            if capability is Capability.REST_API and isinstance(rest_request,Mapping):
                candidate=rest_request.get(tool.name,rest_request)
                request=dict(candidate) if isinstance(candidate,Mapping) else None
                if request is not None:
                    request["__approved__"]=bool(explicitly_approved)
                connector=rest_connector
            if capability is Capability.CALENDAR and tool.name in {"calendar.event.create","calendar.event.update","calendar.event.cancel"} and isinstance(request,dict):request["approved"]=bool(explicitly_approved)
            if capability is Capability.BROWSER and isinstance(browser_request,Mapping):candidate=browser_request.get(tool.name,browser_request);request=dict(candidate) if isinstance(candidate,Mapping) else None;connector=browser_connector
            if capability is Capability.BROWSER and isinstance(request,dict):request["operation"]={"browser.open":"open","browser.click":"click","browser.extract":"extract"}[tool.name]
            result=run_safe_operation(operation,root,timeout_seconds=timeout,output_limit=output,web_connector=connector if capability is Capability.WEB_RESEARCH else None,web_request=request if capability is Capability.WEB_RESEARCH else None,rest_connector=connector if capability is Capability.REST_API else None,rest_request=request if capability is Capability.REST_API else None,workspace_connector=connector if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE,Capability.WORKSPACE_SHELL} else None,workspace_request=request if capability in {Capability.READ_FILE,Capability.FILES_WORKSPACE,Capability.WORKSPACE_SHELL} else None,gmail_connector=connector if capability is Capability.EMAIL else None,gmail_request=request if capability is Capability.EMAIL else None,calendar_connector=connector if capability is Capability.CALENDAR else None,calendar_request=request if capability is Capability.CALENDAR else None,browser_connector=connector if capability is Capability.BROWSER else None,browser_request=request if capability is Capability.BROWSER else None);results.append(result);_audit(audit_path,execution_id,ExecutionState.RUNNING,tool=tool.name,step_id=step.step_id,attempt=attempt+1,result="success" if result.success else "failure",verification=result.verification_status,event="tool_result")
            if result.success and result.verification_status=="verified":break
        else:
            _audit(audit_path,execution_id,ExecutionState.FAILED,tool=tool.name,reason="bounded retries exhausted")
            checkpoint_store.save(execution_id=execution_id,task_digest=task_digest,plan_digest=plan_digest,authorization_digest=authorization_digest,state=ExecutionState.FAILED.value,completed_step_ids=tuple(s.step_id for s in plan.steps if s.step_id in completed_step_ids),total_attempts=total_attempts)
            return ExecutionResult(ExecutionState.FAILED,f"tool execution failed after bounded retries: {tool.name}",total_attempts,tuple(results),str(audit_path))
        completed_step_ids.add(step.step_id)
        checkpoint_store.save(execution_id=execution_id,task_digest=task_digest,plan_digest=plan_digest,authorization_digest=authorization_digest,state=ExecutionState.RUNNING.value,completed_step_ids=tuple(s.step_id for s in plan.steps if s.step_id in completed_step_ids),total_attempts=total_attempts)
        _audit(audit_path,execution_id,ExecutionState.RUNNING,event="checkpoint_saved",tool=tool.name,step_id=step.step_id,attempts=total_attempts)
        _remember(memory,project,tool=tool.name,execution_id=execution_id,outcome="verified",attempts=total_attempts)
    if not completed_step_ids or len(completed_step_ids)<len(plan.steps):
        checkpoint_store.save(execution_id=execution_id,task_digest=task_digest,plan_digest=plan_digest,authorization_digest=authorization_digest,state=ExecutionState.FAILED.value,completed_step_ids=tuple(s.step_id for s in plan.steps if s.step_id in completed_step_ids),total_attempts=total_attempts)
        return ExecutionResult(ExecutionState.FAILED,"post-action verification failed",total_attempts,tuple(results),str(audit_path))
    checkpoint_store.save(execution_id=execution_id,task_digest=task_digest,plan_digest=plan_digest,authorization_digest=authorization_digest,state=ExecutionState.VERIFIED.value,completed_step_ids=tuple(s.step_id for s in plan.steps),total_attempts=total_attempts)
    _audit(audit_path,execution_id,ExecutionState.VERIFIED,attempts=total_attempts,event="checkpoint_verified");_remember(memory,project,execution_id=execution_id,outcome="verified",attempts=total_attempts);return ExecutionResult(ExecutionState.VERIFIED,"all planned actions executed and verified through the existing sandbox",total_attempts,tuple(results),str(audit_path))
