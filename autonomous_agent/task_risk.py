from __future__ import annotations

from autonomous_agent.tool_registry import RiskLevel, ToolSpec
from .task_plan_models import PlanRisk


_RISK_ORDER = {PlanRisk.LOW: 0, PlanRisk.MEDIUM: 1, PlanRisk.HIGH: 2, PlanRisk.CRITICAL: 3}


def risk_for_tool(tool: ToolSpec) -> PlanRisk:
    return PlanRisk(tool.risk_level.value)


def aggregate_risk(tools: tuple[ToolSpec, ...]) -> PlanRisk:
    if not tools:
        return PlanRisk.LOW
    return max((risk_for_tool(tool) for tool in tools), key=lambda risk: _RISK_ORDER[risk])
