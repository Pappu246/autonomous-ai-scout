#!/usr/bin/env python3
"""Finish the remaining Autonomous AI Scout release gates safely.

This helper is intentionally conservative:
- it verifies the remote branch head before dispatching;
- it dispatches only the explicitly requested authoritative workflows;
- it waits for a run whose head SHA exactly matches the verified branch head;
- Gate 5 protection application requires an explicit --apply-gate5 flag;
- it never merges, tags, deploys, or publishes a release.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


GATE3 = "gate3-coding-provider-smoke.yml"
GATE4 = "gate4-external-connector-smoke.yml"
GATE5 = "gate5-github-admin-readiness.yml"


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    head_sha: str
    status: str
    conclusion: str | None
    event: str


def run_cmd(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stdout}\n{result.stderr}".strip()
        )
    return result.stdout.strip()


def require_gh() -> None:
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI (gh) is required for authoritative workflow dispatch.")
    run_cmd("gh", "auth", "status")


def remote_head(repository: str, branch: str) -> str:
    owner, name = repository.split("/", 1)
    out = run_cmd(
        "git",
        "ls-remote",
        f"https://github.com/{owner}/{name}.git",
        f"refs/heads/{branch}",
    )
    if not out:
        raise RuntimeError(f"could not resolve remote head for {repository}:{branch}")
    return out.split()[0]


def dispatch(workflow: str, branch: str, repository: str, *, apply_gate5: bool = False) -> None:
    args = ["gh", "workflow", "run", workflow, "--repo", repository, "--ref", branch]
    if workflow == GATE5:
        args += ["-f", f"apply_protection={'true' if apply_gate5 else 'false'}"]
    run_cmd(*args)


def list_runs(workflow: str, branch: str, repository: str) -> list[WorkflowRun]:
    raw = run_cmd(
        "gh", "run", "list",
        "--repo", repository,
        "--workflow", workflow,
        "--branch", branch,
        "--limit", "30",
        "--json", "databaseId,headSha,status,conclusion,event",
    )
    rows: list[dict[str, Any]] = json.loads(raw or "[]")
    return [
        WorkflowRun(
            run_id=int(row["databaseId"]),
            head_sha=str(row["headSha"]),
            status=str(row["status"]),
            conclusion=row.get("conclusion"),
            event=str(row.get("event", "")),
        )
        for row in rows
    ]


def wait_for_exact(workflow: str, branch: str, repository: str, head_sha: str, timeout: int) -> WorkflowRun:
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = [x for x in list_runs(workflow, branch, repository) if x.head_sha == head_sha]
        if matches:
            latest = matches[0]
            print(
                f"{workflow}: run={latest.run_id} head={latest.head_sha[:12]} "
                f"status={latest.status} conclusion={latest.conclusion}"
            )
            if latest.status == "completed":
                return latest
        time.sleep(8)
    raise TimeoutError(
        f"timed out waiting for {workflow} on exact SHA {head_sha}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=CURRENT_REPO)
    parser.add_argument("--branch", default="feat/mission-control-ui")
    parser.add_argument("--gate3", action="store_true", help="dispatch the authoritative Gate 3 coding-provider smoke")
    parser.add_argument("--gate4", action="store_true", help="dispatch the authoritative Gate 4 smoke")
    parser.add_argument(
        "--gate5",
        action="store_true",
        help="dispatch Gate 5; read-only verification unless --apply-gate5 is also supplied",
    )
    parser.add_argument(
        "--apply-gate5",
        action="store_true",
        help="explicitly authorize Gate 5 to apply main-branch protection",
    )
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    require_gh()
    head = remote_head(args.repository, args.branch)
    print(f"Verified remote head: {args.repository}:{args.branch} -> {head}")

    if args.apply_gate5 and not args.gate5:
        raise RuntimeError("--apply-gate5 requires --gate5")

    if args.check_only:
        for workflow in (GATE3, GATE4, GATE5):
            matches = [x for x in list_runs(workflow, args.branch, args.repository) if x.head_sha == head]
            print(f"{workflow}: exact-head-runs={len(matches)}")
            for item in matches[:3]:
                print(
                    f"  run={item.run_id} event={item.event} "
                    f"status={item.status} conclusion={item.conclusion}"
                )
        return 0

    if not args.gate3 and not args.gate4 and not args.gate5:
        raise RuntimeError("select at least one of --gate3, --gate4 or --gate5")

    if args.gate3:
        print("Dispatching Gate 3 on exact verified branch head…")
        dispatch(GATE3, args.branch, args.repository)
        result = wait_for_exact(GATE3, args.branch, args.repository, head, args.timeout)
        if result.conclusion != "success":
            raise RuntimeError(
                f"Gate 3 did not pass on exact SHA {head}: run {result.run_id} "
                f"conclusion={result.conclusion}"
            )
        print(f"Gate 3 PASS: run {result.run_id}")

    if args.gate4:
        print("Dispatching Gate 4 on exact verified branch head…")
        dispatch(GATE4, args.branch, args.repository)
        result = wait_for_exact(GATE4, args.branch, args.repository, head, args.timeout)
        if result.conclusion != "success":
            raise RuntimeError(
                f"Gate 4 did not pass on exact SHA {head}: run {result.run_id} "
                f"conclusion={result.conclusion}"
            )
        print(f"Gate 4 PASS: run {result.run_id}")

    if args.gate5:
        if args.apply_gate5:
            print("Dispatching Gate 5 with explicit protection application authorization…")
        else:
            print("Dispatching Gate 5 in read/verify-only mode…")
        dispatch(GATE5, args.branch, args.repository, apply_gate5=args.apply_gate5)
        result = wait_for_exact(GATE5, args.branch, args.repository, head, args.timeout)
        if result.conclusion != "success":
            raise RuntimeError(
                f"Gate 5 did not pass on exact SHA {head}: run {result.run_id} "
                f"conclusion={result.conclusion}"
            )
        print(f"Gate 5 PASS: run {result.run_id}")

    print("Remaining release gates executed successfully for the verified branch head.")
    print("No merge, tag, deployment, or release was performed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
