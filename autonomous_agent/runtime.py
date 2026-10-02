from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capability_policy import Capability
from .cross_project_memory import CrossProjectMemory
from .execution_engine import ExecutionResult, ExecutionState
from .run_journal import append_run_record, make_run_record, read_run_records, summarize_run_records
from .task_core import AutonomousTaskCore
from .task_plan_models import TaskPlan
from .tool_registry import REGISTRY, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "state" / "runtime_execution.jsonl"
JOURNAL_PATH = ROOT / "state" / "runtime_runs.jsonl"


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
    computer_connector: Any = None,
    computer_request: Mapping[str, Any] | None = None,
    memory: CrossProjectMemory | None = None,
    project: str = "local",
    granted: Iterable[Capability | str] | None = None,
    explicitly_approved: bool = False,
) -> ExecutionResult:
    execution_id = execution_id or os.urandom(8).hex()
    checkpoint_path = checkpoint_path or root / "state" / "runtime_checkpoints" / f"{execution_id}.json"
    core = AutonomousTaskCore(registry=registry)
    if granted is None and not explicitly_approved:
        prepared = core.prepare(task)
    else:
        prepared = core.prepare(
            task,
            granted=granted,
            explicitly_approved=explicitly_approved,
        )
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
        memory=memory,
        project=project,
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
        computer_connector=computer_connector,
        computer_request=computer_request,
        explicitly_approved=explicitly_approved,
    )
    append_run_record(
        journal_path,
        make_run_record(execution_id=execution_id, task=task, result=result),
    )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Autonomous AI Scout task")
    parser.add_argument("task", nargs="?", default=os.getenv("TASK_REQUEST", "inspect repository"))
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--audit", default=str(AUDIT_PATH))
    parser.add_argument("--journal", default=str(JOURNAL_PATH))
    parser.add_argument("--history", action="store_true", help="print bounded runtime history summary and recent records")
    parser.add_argument("--history-limit", type=int, default=20, help="number of recent valid history records to print")
    parser.add_argument("--computer", action="store_true", help="enable the bounded Windows computer connector")
    parser.add_argument("--approve", action="store_true", help="explicitly approve state-changing actions for this run")
    parser.add_argument("--max-turns", type=int, default=20, help="maximum native computer-use model turns")
    parser.add_argument("--action-budget", type=int, default=50, help="maximum bounded desktop actions")
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

    computer_connector = None
    computer_request = None
    granted = None
    if args.computer:
        from .computer.connector import BoundedComputerConnector
        from .computer.models import ActionBudget
        computer_connector = BoundedComputerConnector(
            budget=ActionBudget(limit=max(1, min(int(args.action_budget), 200)))
        )
        from .capability_policy import Capability as _Capability
        granted = (_Capability.COMPUTER,)
        computer_request = {
            "computer.use": {
                "task": args.task,
                "max_turns": max(1, min(int(args.max_turns), 20)),
            },
            "computer.use:credref": None,
        }
    result = run_task(
        args.task,
        root=Path(args.root).resolve(),
        audit_path=Path(args.audit).resolve(),
        journal_path=Path(args.journal).resolve(),
        computer_connector=computer_connector,
        computer_request=computer_request,
        granted=granted,
        explicitly_approved=bool(args.approve),
    )
    print(f"state={result.state.value}")
    print(f"reason={result.reason}")
    print(f"attempts={result.attempts}")
    for item in result.results:
        print(f"operation={item.operation} success={item.success} verification={item.verification_status}")
        if item.output:
            print(item.output)
    print(f"audit={result.audit_path}")
    return 0 if result.state is ExecutionState.VERIFIED else 1


if __name__ == "__main__":
    raise SystemExit(main())
