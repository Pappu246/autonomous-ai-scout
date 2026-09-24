from __future__ import annotations
import hashlib,json
from typing import Iterable
from .capability_policy import Capability, DENIED_CAPABILITIES
from .task_decomposer import decompose_task
from .task_intent import classify_intent
from .task_plan_models import PlanRisk,TaskAuditRecord,TaskPlan,TaskStep
from .task_risk import aggregate_risk
from .tool_registry import ToolRegistry,REGISTRY
from .tool_router import DynamicToolRouter


def _digest(task, intent, tool_names):
    return hashlib.sha256(
        json.dumps(
            {"task": task, "intent": intent, "tools": tuple(tool_names)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
def _selection(registry,task,intent):
    return DynamicToolRouter(registry).select_names(task,intent)

def _select_tools(registry,intent,task=""):
    selection=_selection(registry,task,intent)
    return tuple(tool for name in selection.tool_names if (tool:=registry.get(name)) is not None)

def _required_tools(task,intent,registry=REGISTRY):
    return _selection(registry,task,intent).candidate_names
def candidate_tool_names(task: str, registry: ToolRegistry = REGISTRY) -> tuple[str, ...]:
    """Return the canonical planner's selected tool names without authorizing execution."""
    raw = " ".join(task.strip().split())
    intent = classify_intent(raw)
    return tuple(tool.name for tool in _select_tools(registry, intent, raw))


def default_grants_for_task(
    task: str | object,
    registry: ToolRegistry = REGISTRY,
    *,
    explicitly_approved: bool = False,
) -> tuple[Capability, ...]:
    """Grant safe autonomous capabilities, plus safe registered capabilities after explicit approval."""
    raw = task.task if hasattr(task, "task") and isinstance(getattr(task, "task"), str) else str(task)
    intent = classify_intent(raw)
    values: list[Capability] = []
    seen: set[Capability] = set()
    for tool in _select_tools(registry, intent, raw):
        if not tool.safe_autonomous and not explicitly_approved:
            continue
        capability = Capability(tool.capability)
        if capability in DENIED_CAPABILITIES:
            continue
        if capability not in seen:
            seen.add(capability)
            values.append(capability)
    return tuple(values)


def plan_task(task:str,*,granted:Iterable[Capability|str]=(),explicitly_approved=False,sandbox_available=True,audit_available=True,registry:ToolRegistry=REGISTRY):
    raw=" ".join(task.strip().split());intent=classify_intent(raw);required=_required_tools(raw,intent,registry);selection=_selection(registry,raw,intent);selected=_select_tools(registry,intent,raw)
    if not required:reason,executable="No registered executable tool mapping exists; plan fails closed.",False
    elif missing:=tuple(name for name in required if registry.get(name) is None):reason,executable=f"Required registered tools are missing: {', '.join(missing)}; plan fails closed.",False
    else:reason,executable="All selected tools are registered; authorization will be checked before execution.",True
    descriptions=decompose_task(raw,intent);steps=[]
    for index,tool in enumerate(selected,1):
        description=descriptions[min(index-1,len(descriptions)-1)] if descriptions else f"Run {tool.name}.";decision=registry.authorize(tool.name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=audit_available)
        if not decision.allowed:
            if (
                not explicitly_approved
                and Capability(tool.capability) not in DENIED_CAPABILITIES
                and tool.approval_requirement.value != "none"
            ):
                # Keep the overall plan executable so safe/read-only steps can
                # complete first. The execution engine stops exactly at the
                # approval-gated step and persists its checkpoint for resume.
                reason = f"Explicit approval required for {tool.name}: {decision.reason}"
            else:
                executable, reason = False, f"Authorization blocked for {tool.name}: {decision.reason}"
        steps.append(TaskStep(f"step-{index}",description,tool.name,PlanRisk(tool.risk_level.value),"authorized" if decision.allowed else "blocked","execute only through the existing registered capability/sandbox/lifecycle boundary","verify tool result before proceeding and retain audit record"))
    digest=_digest(raw,intent.value,tuple(s.tool_name for s in steps));return TaskPlan(raw,intent,tuple(steps),aggregate_risk(selected),executable,reason,TaskAuditRecord(raw,intent,tuple(s.step_id for s in steps),executable,digest))
