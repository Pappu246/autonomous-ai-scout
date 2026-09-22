from __future__ import annotations

import argparse
import json
from pathlib import Path

from .approval import pending_actions
from .approval_store import create_approval, reject_action
from .approved_coding_bridge import (
    execute_persisted_approved_run,
    mark_coding_run_approved,
    mark_coding_run_rejected,
)
from .coding_run_store import CodingRunNotFound, CodingRunStore, CodingRunStoreError
from .github_worker import build_github_worker_from_env
from .action_queue import load_queue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit approval operations for Autonomous AI Scout")
    parser.add_argument("--queue", default="state/approval_queue.json")
    parser.add_argument("--audit", default="state/approval_audit.jsonl")
    parser.add_argument("--approvals", default="state/approvals")
    parser.add_argument("--runs", default="state/coding_runs", help="durable coding-run store")
    parser.add_argument("--lifecycle", default="state/lifecycle_ledger.jsonl")
    parser.add_argument("--claims", default="state/approval_claims")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pending", help="list pending approval ids")

    approve = sub.add_parser("approve", help="explicitly approve one pending action")
    approve.add_argument("action_id")

    reject = sub.add_parser("reject", help="explicitly reject one pending action")
    reject.add_argument("action_id")

    inspect = sub.add_parser("inspect", help="show non-secret status for one persisted coding run")
    inspect.add_argument("action_id")

    execute = sub.add_parser("execute", help="execute one explicitly approved persisted coding run")
    execute.add_argument("action_id")

    observe = sub.add_parser("observe", help="observe an existing draft PR and persist its CI status")
    observe.add_argument("action_id")

    sub.add_parser("status", help="display global system summary")

    args = parser.parse_args(argv)
    queue = Path(args.queue)
    audit = Path(args.audit)
    approvals = Path(args.approvals)
    runs = CodingRunStore(Path(args.runs))
    lifecycle = Path(args.lifecycle)
    claims = Path(args.claims)

    if args.command == "pending":
        for action in pending_actions(queue):
            print(f"{action.id} [{action.risk}] {action.task}")
        return 0

    if args.command == "approve":
        try:
            record = create_approval(queue, approvals, args.action_id, audit_path=audit)
            try:
                action = next(item for item in load_queue(queue) if item.id == args.action_id)
                mark_coding_run_approved(runs, action=action, lifecycle_path=lifecycle)
            except CodingRunNotFound:
                pass
            print(f"approved={record.action_id}")
            print(f"expires_at={record.expires_at}")
            return 0
        except (KeyError, StopIteration, ValueError, CodingRunStoreError) as exc:
            print(f"error={exc}")
            return 2

    if args.command == "reject":
        try:
            reject_action(queue, args.action_id, audit_path=audit)
            try:
                mark_coding_run_rejected(runs, action_id=args.action_id, lifecycle_path=lifecycle)
            except CodingRunNotFound:
                pass
            print(f"rejected={args.action_id}")
            return 0
        except (KeyError, ValueError, CodingRunStoreError) as exc:
            print(f"error={exc}")
            return 2

    if args.command == "inspect":
        try:
            print(json.dumps(runs.summary(runs.find_by_action(args.action_id)), indent=2, sort_keys=True))
            return 0
        except CodingRunStoreError as exc:
            print(f"error={exc}")
            return 2

    if args.command == "observe":
        try:
            record = runs.find_by_action(args.action_id)
            pull_request = record.execution.pull_request
            if record.state.value != "EXECUTED" or not pull_request:
                print("error=executed coding run with a recorded pull request is required")
                return 2
            worker = build_github_worker_from_env(claim_store=claims)
            result = worker.observe(record.repository, pull_request)
            runs.record_observation(record.run_id, worker_state=result.state, reason=result.reason, ci_status=result.ci_status)
        except CodingRunStoreError as exc:
            print(f"error={exc}")
            return 2
        print(f"state={result.state}")
        print(f"reason={result.reason}")
        if result.pull_request:
            print(f"pull_request={result.pull_request}")
        if result.ci_status:
            print(f"ci_status={result.ci_status}")
        return 0

    if args.command == "status":
        print(f"pending={len(list(pending_actions(queue)))}")
        print(f"runs={len(runs.list_all())}")
        return 0

    try:
        worker = build_github_worker_from_env(claim_store=claims)
        result = execute_persisted_approved_run(
            args.action_id,
            store=runs,
            queue_path=queue,
            approval_dir=approvals,
            audit_path=audit,
            lifecycle_path=lifecycle,
            claim_store=claims,
            worker=worker,
        )
    except CodingRunStoreError as exc:
        print(f"error={exc}")
        return 2
    print(f"state={result.state}")
    print(f"reason={result.reason}")
    if result.pull_request:
        print(f"pull_request={result.pull_request}")
    if result.ci_status:
        print(f"ci_status={result.ci_status}")
    return 0 if result.state == "draft_pr_created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
