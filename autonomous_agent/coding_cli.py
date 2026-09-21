from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import replace
from pathlib import Path

from .coding_run_store import CodingRunStore, CodingRunStoreError
from .approved_coding_bridge import persist_ready_coding_run
from .continuous_improvement import build_proposal
from .engineering_pipeline import build_local_coding_pipeline, summarize_approval
from .improvement_actions import enqueue_improvement
from .models import ProjectFinding


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded AI coding pipeline against a local checkout."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--project", required=True)
    parser.add_argument("--severity", default="medium")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--affected", nargs="*", default=())
    parser.add_argument("--confidence", type=float, default=0.8)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--queue", default="state/approval_queue.json")
    parser.add_argument("--runs", default="state/coding_runs")
    parser.add_argument("--lifecycle", default="state/lifecycle_ledger.jsonl")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--head-branch", default="")
    parser.add_argument("--expected-head-sha", required=True, help="exact 40-character base-branch HEAD approved for this run")
    parser.add_argument("--run-id", default="", help="optional path-safe durable run identifier")
    args = parser.parse_args(argv)

    pipeline = build_local_coding_pipeline(
        Path(args.root),
        allow_paid=args.allow_paid,
        timeout_seconds=args.timeout,
        max_revisions=args.max_revisions,
    )
    finding = ProjectFinding(
        repository=args.project,
        severity=args.severity,
        title=args.title,
        detail=args.detail,
        recommendation=args.recommendation,
        confidence=max(0.0, min(1.0, args.confidence)),
    )
    proposal = build_proposal(args.project, finding)
    if args.affected:
        proposal = replace(proposal, affected_area=tuple(args.affected))
    run = pipeline.run(proposal)

    print(json.dumps(summarize_approval(run), indent=2, sort_keys=True))
    print(f"status={run.status.value}")
    if run.candidate is None or run.review is None or not run.approval_required or run.status.value != "ready_for_approval":
        return 1

    action = enqueue_improvement(Path(args.queue), proposal)
    if action is None:
        print("persistence_error=approval-gated improvement action was not created")
        return 1
    run_id = args.run_id or secrets.token_hex(16)
    head_branch = args.head_branch.strip() or f"improvement/{action.id}"
    try:
        record = persist_ready_coding_run(
            CodingRunStore(Path(args.runs)),
            lifecycle_path=Path(args.lifecycle),
            run_id=run_id,
            proposal=proposal,
            run=run,
            action=action,
            repository=args.project,
            base_branch=args.base_branch,
            head_branch=head_branch,
            expected_head_sha=args.expected_head_sha,
        )
    except CodingRunStoreError as exc:
        print(f"persistence_error={exc}")
        return 1
    print(f"run_id={record.run_id}")
    print(f"action_id={record.action_id}")
    print(f"run_state={record.state.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
