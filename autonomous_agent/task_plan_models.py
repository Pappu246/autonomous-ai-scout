from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping
class TaskIntent(str,Enum):
    RESEARCH="research";WORKSPACE="workspace";INSPECT="inspect";TEST="test";IMPROVE="improve";CHANGE="change";AUTOMATE="automate";UNKNOWN="unknown"
class PlanRisk(str,Enum):LOW="low";MEDIUM="medium";HIGH="high";CRITICAL="critical"
@dataclass(frozen=True)
class TaskStep:step_id:str;description:str;tool_name:str;risk:PlanRisk;authorization:str;execution_boundary:str;verification:str
@dataclass(frozen=True)
class TaskAuditRecord:task:str;intent:TaskIntent;step_ids:tuple[str,...];authorized:bool;plan_digest:str
@dataclass(frozen=True)
class TaskPlan:
    task:str;intent:TaskIntent;steps:tuple[TaskStep,...];risk:PlanRisk;executable:bool;reason:str;audit:TaskAuditRecord
    @property
    def metadata(self)->Mapping[str,object]:return {"intent":self.intent.value,"risk":self.risk.value,"executable":self.executable,"steps":len(self.steps),"plan_digest":self.audit.plan_digest}
