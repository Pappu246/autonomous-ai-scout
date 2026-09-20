from __future__ import annotations

import argparse
from pathlib import Path

from .approval import pending_actions
from .approval_store import create_approval, reject_action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit approval operations for Autonomous AI Scout")
    parser.add_argument("--queue", default="state/approval_queue.json")
    parser.add_argument("--audit", default="state/approval_audit.jsonl")
    parser.add_argument("--approvals", default="state/approvals")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pending", help="list pending approval ids")

    approve = sub.add_parser("approve", help="explicitly approve one pending action")
    approve.add_argument("action_id")

    reject = sub.add_parser("reject", help="explicitly reject one pending action")
    reject.add_argument("action_id")

    args = parser.parse_args(argv)
    queue = Path(args.queue)
    audit = Path(args.audit)
    approvals = Path(args.approvals)

    if args.command == "pending":
        for action in pending_actions(queue):
            print(f"{action.id} [{action.risk}] {action.task}")
        return 0

    if args.command == "approve":
        record = create_approval(queue, approvals, args.action_id, audit_path=audit)
        print(f"approved={record.action_id}")
        print(f"expires_at={record.expires_at}")
        return 0

    reject_action(queue, args.action_id, audit_path=audit)
    print(f"rejected={args.action_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
