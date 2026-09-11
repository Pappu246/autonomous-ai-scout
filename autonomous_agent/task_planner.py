from __future__ import annotations
import hashlib,json
from typing import Iterable
from .capability_policy import Capability
from .task_decomposer import decompose_task
from .task_intent import classify_intent
from .task_plan_models import PlanRisk,TaskAuditRecord,TaskPlan,TaskStep
from .task_risk import aggregate_risk
from .tool_registry import ToolRegistry,REGISTRY
_INTENT_TO_TOOLS={"research":("web.search","web.read","web.extract","web.compare"),"workspace":("filesystem.list","filesystem.read","filesystem.write","filesystem.transform"),"calendar":("calendar.list","calendar.read","calendar.find_free_time","calendar.event.create","calendar.event.update","calendar.event.cancel"),"inspect":("github.inspect",),"test":("github.inspect","tests.run"),"improve":("github.inspect","tests.run","github.change"),"change":("github.inspect","tests.run","github.change"),"automate":(),"unknown":()}
def _digest(task,intent,tool_names):return hashlib.sha256(json.dumps({"task":task,"intent":intent,"tools":tool_names},sort_keys=True,separators=(",",":")).encode()).hexdigest()
def _select_tools(registry,intent):return tuple(tool for name in _INTENT_TO_TOOLS.get(intent,()) if (tool:=registry.get(name)) is not None)
def plan_task(task:str,*,granted:Iterable[Capability|str]=(),explicitly_approved=False,sandbox_available=True,audit_available=True,registry:ToolRegistry=REGISTRY):
    raw=" ".join(task.strip().split());intent=classify_intent(raw);required=_INTENT_TO_TOOLS.get(intent.value,());selected=_select_tools(registry,intent);descriptions=decompose_task(raw,intent);missing=tuple(name for name in required if registry.get(name) is None)
    if intent.value in {"automate","unknown"}:reason,executable="No registered executable tool mapping exists; plan fails closed.",False
    elif missing:reason,executable=f"Required registered tools are missing: {', '.join(missing)}; plan fails closed.",False
    else:reason,executable="All selected tools are registered; authorization will be checked before execution.",True
    steps=[]
    for index,tool in enumerate(selected,1):
        description=descriptions[min(index-1,len(descriptions)-1)] if descriptions else f"Run {tool.name}.";decision=registry.authorize(tool.name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=audit_available)
        if not decision.allowed:executable,reason=False,f"Authorization blocked for {tool.name}: {decision.reason}"
        steps.append(TaskStep(f"step-{index}",description,tool.name,PlanRisk(tool.risk_level.value),"authorized" if decision.allowed else "blocked","execute only through the existing registered capability/sandbox/lifecycle boundary","verify tool result before proceeding and retain audit record"))
    digest=_digest(raw,intent.value,tuple(s.tool_name for s in steps));return TaskPlan(raw,intent,tuple(steps),aggregate_risk(selected),executable,reason,TaskAuditRecord(raw,intent,tuple(s.step_id for s in steps),executable,digest))
