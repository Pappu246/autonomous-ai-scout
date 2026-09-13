from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capability_policy import Capability
from .execution_engine import ExecutionResult, ExecutionState, execute_plan
from .run_journal import append_run_record, make_run_record
from .task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan
from .tool_registry import REGISTRY, ToolRegistry
from .workflow_engine import WorkflowDefinition, WorkflowEngine

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "state" / "runtime_execution.jsonl"
JOURNAL_PATH = ROOT / "state" / "runtime_runs.jsonl"

def _plan_for_request(task: str, registry: ToolRegistry = REGISTRY) -> tuple[TaskPlan, tuple[Capability, ...]]:
    text = " ".join(task.strip().split())
    lowered = text.lower()
    if not text:
        digest = TaskAuditRecord("", TaskIntent.UNKNOWN, (), False, "")
        return TaskPlan("", TaskIntent.UNKNOWN, (), PlanRisk.LOW, False, "No task supplied.", digest), ()
    tools: list[str]
    grants: tuple[Capability, ...]
    if any(word in lowered for word in ("test", "tests", "pytest")):
        tools = ["github.inspect", "tests.run"]; grants = (Capability.INSPECT, Capability.TEST)
    elif any(word in lowered for word in ("inspect", "analyze", "analyse", "audit")):
        tools = ["github.inspect"]; grants = (Capability.INSPECT,)
    elif any(word in lowered for word in ("research", "browse", "web")):
        tools = ["web.search", "web.read"]; grants = (Capability.WEB_RESEARCH,)
    elif any(word in lowered for word in ("email", "mail")):
        tools = ["email.search", "email.read"]; grants = (Capability.EMAIL,)
    elif any(word in lowered for word in ("calendar", "schedule")):
        tools = ["calendar.list", "calendar.find_free_time"]; grants = (Capability.CALENDAR,)
    else:
        tools = ["github.inspect"]; grants = (Capability.INSPECT,)
    workflow = WorkflowDefinition(name="runtime-request", task=text, tool_names=tuple(tools))
    return WorkflowEngine(registry).plan(workflow, granted=grants), grants

def run_task(task: str, *, root: Path = ROOT, audit_path: Path = AUDIT_PATH, journal_path: Path = JOURNAL_PATH, execution_id: str | None = None, registry: ToolRegistry = REGISTRY, browser_connector: Any = None, browser_request: Mapping[str, Any] | None = None, web_connector: Any = None, web_request: Mapping[str, Any] | None = None, workspace_connector: Any = None, workspace_request: Mapping[str, Any] | None = None, gmail_connector: Any = None, gmail_request: Mapping[str, Any] | None = None, calendar_connector: Any = None, calendar_request: Mapping[str, Any] | None = None) -> ExecutionResult:
    execution_id = execution_id or os.urandom(8).hex()
    plan, grants = _plan_for_request(task, registry)
    if not plan.executable:
        result = ExecutionResult(ExecutionState.BLOCKED, plan.reason, 0, (), str(audit_path))
        append_run_record(journal_path, make_run_record(execution_id=execution_id, task=task, result=result))
        return result
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    result = execute_plan(plan, root, granted=grants, audit_path=audit_path, execution_id=execution_id, registry=registry, browser_connector=browser_connector, browser_request=browser_request, web_connector=web_connector, web_request=web_request, workspace_connector=workspace_connector, workspace_request=workspace_request, gmail_connector=gmail_connector, gmail_request=gmail_request, calendar_connector=calendar_connector, calendar_request=calendar_request)
    append_run_record(journal_path, make_run_record(execution_id=execution_id, task=task, result=result))
    return result

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Autonomous AI Scout task")
    parser.add_argument("task", nargs="?", default=os.getenv("TASK_REQUEST", "inspect repository"))
    parser.add_argument("--root", default=str(ROOT)); parser.add_argument("--audit", default=str(AUDIT_PATH)); parser.add_argument("--journal", default=str(JOURNAL_PATH))
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run_task(args.task, root=Path(args.root).resolve(), audit_path=Path(args.audit).resolve(), journal_path=Path(args.journal).resolve())
    print(f"state={result.state.value}"); print(f"reason={result.reason}"); print(f"attempts={result.attempts}")
    for item in result.results:
        print(f"operation={item.operation} success={item.success} verification={item.verification_status}")
        if item.output: print(item.output)
    print(f"audit={result.audit_path}")
    return 0 if result.state is ExecutionState.VERIFIED else 1

if __name__ == "__main__": raise SystemExit(main())
