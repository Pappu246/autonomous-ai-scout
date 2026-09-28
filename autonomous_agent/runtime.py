from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capability_policy import Capability
from .execution_engine import ExecutionResult, ExecutionState
from .filesystem_workspace import WorkspaceConnector
from .run_journal import append_run_record, make_run_record, read_run_records, summarize_run_records
from .task_core import AutonomousTaskCore
from .task_plan_models import TaskPlan
from .tool_registry import REGISTRY, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "state" / "runtime_execution.jsonl"
JOURNAL_PATH = ROOT / "state" / "runtime_runs.jsonl"

def _workspace_request_for_task(task: str, plan: TaskPlan) -> Mapping[str, Any] | None:
    """Derive bounded workspace requests only for positive operations in the task text."""
    import re
    text = task.strip()
    selected = {step.tool_name for step in plan.steps}
    requests: dict[str, Any] = {}
    negative = re.compile(r"\b(?:do not|don't|never|without)\b", re.I)
    def positive_match(pattern: str) -> re.Match[str] | None:
        match = re.search(pattern, text, re.I | re.S)
        if match is None or negative.search(match.group(0)):
            return None
        return match
    if "workspace.shell" in selected:
        match = positive_match(r"(?:exactly\s+this\s+(?:safe\s+)?validation\s+command|command)\s*:\s*(.+)$")
        if match:
            command_text = match.group(1).strip().strip("`")
            py_compile = re.match(r"python\s+-m\s+py_compile\s+([A-Za-z0-9_./\\-]+)", command_text, re.I)
            if py_compile:
                requests["workspace.shell"] = {"argv": ("python", "-m", "py_compile", py_compile.group(1).rstrip("."))}
            else:
                simple = re.match(r"(pwd|ls|dir)\b", command_text, re.I)
                if simple:
                    requests["workspace.shell"] = {"argv": (simple.group(1).lower(),)}
    if "filesystem.transform" in selected or re.search(r"(?:transform|modify|replace in)\s+file\b", text, re.I):
        match = positive_match(r"(?:transform|modify|replace in)\s+file\s+([A-Za-z0-9_./\\-]+)\s*:\s*(.*?)\s*->\s*(.*?)$")
        if match:
            requests["filesystem.transform"] = {"operation":"transform","path":match.group(1).rstrip("."),"find":match.group(2),"replace":match.group(3)}
    if "filesystem.write" in selected or re.search(r"(?:write|create|save)\s+(?:a|an|the)?\s*file\b", text, re.I):
        match = re.search(r"(?:write|create|save)\s+.*?\bfile\s+(?:at|named|called)?\s*([A-Za-z0-9_./\\-]+)\s*(?:containing|with(?:\s+contents?)?)\s*:?[ \t]*(.+)$", text, re.I | re.S)
        if match:
            content = re.split(r"\s+(?:do not|don't|never)\b", match.group(2), maxsplit=1, flags=re.I)[0].rstrip().rstrip(".")
            requests["filesystem.write"] = {"operation":"write","path":match.group(1).rstrip("."),"content":content}
    if ("filesystem.read" in selected or re.search(r"(?:read|open)\s+(?:the\s+)?file\b", text, re.I) or re.search(r"(?:read|open)\s+(?:the\s+)?[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_-]+\b", text, re.I)):
        match = re.search(r"(?:read|open)\s+(?:the\s+)?file\s+([A-Za-z0-9_./\\-]+)", text, re.I)
        if not match:
            match = re.search(r"(?:read|open)\s+(?:the\s+)?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_-]+)\b", text, re.I)
        if match and not negative.search(match.group(0)):
            requests["filesystem.read"] = {"operation":"read","path":match.group(1).rstrip(".")}
    if "filesystem.list" in selected or re.search(r"\b(?:list|enumerate)\s+(?:the\s+)?(?:files|directory|folder|workspace)\b", text, re.I):
        if re.search(r"\b(?:list|enumerate)\s+(?:the\s+)?(?:files|directory|folder|workspace)\b", text, re.I) or re.search(r"\binspect\s+(?:the\s+)?(?:workspace|directory|folder)\b", text, re.I):
            requests["filesystem.list"] = {"operation":"list","path":"."}
    return requests or None
def _plan_for_request(task: str, registry: ToolRegistry = REGISTRY) -> tuple[TaskPlan, tuple[Capability, ...]]:
    """Compatibility adapter; all task planning flows through AutonomousTaskCore."""
    prepared = AutonomousTaskCore(registry=registry).prepare(task)
    return prepared.plan, prepared.granted

def run_task(
    task: str,
    *,
    root: Path = ROOT,
    audit_path: Path = AUDIT_PATH,
    journal_path: Path = JOURNAL_PATH,
    execution_id: str | None = None,
    checkpoint_path: Path | None = None,
    explicitly_approved: bool = False,
    registry: ToolRegistry = REGISTRY,
    browser_connector: Any = None,
    browser_request: Mapping[str, Any] | None = None,
    web_connector: Any = None,
    web_request: Mapping[str, Any] | None = None,
    workspace_connector: Any = None,
    workspace_request: Mapping[str, Any] | None = None,
    gmail_connector: Any = None,
    gmail_request: Mapping[str, Any] | None = None,
    calendar_connector: Any = None,
    calendar_request: Mapping[str, Any] | None = None,
    rest_connector: Any = None,
    rest_request: Mapping[str, Any] | None = None,
) -> ExecutionResult:
    execution_id = execution_id or os.urandom(8).hex()
    checkpoint_path = checkpoint_path or root / "state" / "runtime_checkpoints" / f"{execution_id}.json"
    core = AutonomousTaskCore(registry=registry)
    prepared = core.prepare(task, explicitly_approved=True) if explicitly_approved else core.prepare(task)
    if workspace_connector is None and any(
        step.tool_name.startswith("filesystem.") or step.tool_name == "workspace.shell"
        for step in prepared.plan.steps
    ):
        workspace_connector = WorkspaceConnector(root)
    if workspace_request is None:
        workspace_request = _workspace_request_for_task(task, prepared.plan)
    if not prepared.plan.executable:
        result = ExecutionResult(
            ExecutionState.BLOCKED,
            prepared.plan.reason,
            0,
            (),
            str(audit_path),
        )
        append_run_record(
            journal_path,
            make_run_record(execution_id=execution_id, task=task, result=result),
        )
        return result

    result = core.execute(
        prepared,
        root,
        audit_path=audit_path,
        execution_id=execution_id,
        checkpoint_path=checkpoint_path,
        explicitly_approved=explicitly_approved,
        browser_connector=browser_connector,
        browser_request=browser_request,
        web_connector=web_connector,
        web_request=web_request,
        workspace_connector=workspace_connector,
        workspace_request=workspace_request,
        gmail_connector=gmail_connector,
        gmail_request=gmail_request,
        calendar_connector=calendar_connector,
        calendar_request=calendar_request,
        rest_connector=rest_connector,
        rest_request=rest_request,
    )
    append_run_record(
        journal_path,
        make_run_record(execution_id=execution_id, task=task, result=result),
    )
    return result

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Autonomous AI Scout task")
    parser.add_argument("task", nargs="?", default=os.getenv("TASK_REQUEST", "inspect repository"))
    parser.add_argument("--root", default=str(ROOT)); parser.add_argument("--audit", default=str(AUDIT_PATH)); parser.add_argument("--journal", default=str(JOURNAL_PATH))
    parser.add_argument("--history", action="store_true", help="print bounded runtime history summary and recent records")
    parser.add_argument("--history-limit", type=int, default=20, help="number of recent valid history records to print")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.history:
        records = read_run_records(Path(args.journal).resolve(), limit=args.history_limit)
        summary = summarize_run_records(records)
        print(f"total={summary['total']}")
        print(f"verified={summary['verified']}")
        print(f"blocked={summary['blocked']}")
        print(f"failed={summary['failed']}")
        for record in records:
            print(f"execution_id={record.execution_id} state={record.state} task={record.task} attempts={record.attempts} results={record.result_count} recorded_at={record.recorded_at}")
        return 0

    result = run_task(args.task, root=Path(args.root).resolve(), audit_path=Path(args.audit).resolve(), journal_path=Path(args.journal).resolve())
    print(f"state={result.state.value}"); print(f"reason={result.reason}"); print(f"attempts={result.attempts}")
    for item in result.results:
        print(f"operation={item.operation} success={item.success} verification={item.verification_status}")
        if item.output: print(item.output)
    print(f"audit={result.audit_path}")
    return 0 if result.state is ExecutionState.VERIFIED else 1

if __name__ == "__main__": raise SystemExit(main())
