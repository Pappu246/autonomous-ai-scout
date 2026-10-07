#!/usr/bin/env python3
"""Finish the remaining Autonomous AI Scout release gates safely.

This helper is intentionally conservative:
- it verifies the remote branch head before dispatching;
- it dispatches only the explicitly requested authoritative workflows;
- it waits for a run whose head SHA exactly matches the verified branch head;
- it observes both push-triggered and workflow-dispatch gate executions;
- Gate 5 protection application requires an explicit --apply-gate5 flag;
- it never merges, tags, deploys, or publishes a release.

Authentication:
- Prefer SCOUT_GITHUB_ADMIN_TOKEN (or GITHUB_TOKEN/GH_TOKEN) via the GitHub
  REST API. This avoids requiring the GitHub CLI on the agent host.
- Fall back to an authenticated gh CLI when no token environment variable is
  available.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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


def github_token() -> str | None:
    for name in ("SCOUT_GITHUB_ADMIN_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def github_api(
    method: str,
    path: str,
    *,
    repository: str,
    body: dict[str, Any] | None = None,
) -> Any:
    token = github_token()
    if not token:
        raise RuntimeError(
            "no GitHub token found; set SCOUT_GITHUB_ADMIN_TOKEN, GITHUB_TOKEN, "
            "or GH_TOKEN, or authenticate the gh CLI"
        )

    url = f"https://api.github.com{path}"
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "autonomous-ai-scout-release-finisher",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub API {method} {path} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API connection failed: {exc.reason}") from exc


def require_auth(repository: str) -> None:
    token = github_token()
    if token:
        owner, name = repository.split("/", 1)
        data = github_api("GET", f"/repos/{owner}/{name}", repository=repository)
        permissions = data.get("permissions") or {}
        if permissions and permissions.get("admin") is not True:
            raise RuntimeError(
                f"GitHub token can access {repository} but does not report admin permission"
            )
        return

    require_gh()


def require_gh() -> None:
    if not shutil.which("gh"):
        raise RuntimeError(
            "GitHub authentication is unavailable: set SCOUT_GITHUB_ADMIN_TOKEN "
            "(preferred) or authenticate the GitHub CLI with gh auth login"
        )
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
    token = github_token()
    if token:
        owner, name = repository.split("/", 1)
        inputs: dict[str, str] = {}
        if workflow == GATE5:
            inputs["apply_protection"] = "true" if apply_gate5 else "false"
        github_api(
            "POST",
            f"/repos/{owner}/{name}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/dispatches",
            repository=repository,
            body={"ref": branch, "inputs": inputs},
        )
        return

    args = ["gh", "workflow", "run", workflow, "--repo", repository, "--ref", branch]
    if workflow == GATE5:
        args += ["-f", f"apply_protection={'true' if apply_gate5 else 'false'}"]
    run_cmd(*args)


def list_runs(workflow: str, branch: str, repository: str) -> list[WorkflowRun]:
    token = github_token()
    if token:
        owner, name = repository.split("/", 1)
        query = urllib.parse.urlencode(
            {"branch": branch, "per_page": "30"}
        )
        data = github_api(
            "GET",
            f"/repos/{owner}/{name}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/runs?{query}",
            repository=repository,
        )
        rows: list[dict[str, Any]] = data.get("workflow_runs") or []
    else:
        raw = run_cmd(
            "gh", "run", "list",
            "--repo", repository,
            "--workflow", workflow,
            "--branch", branch,
            "--limit", "30",
            "--json", "databaseId,headSha,status,conclusion,event",
        )
        rows = json.loads(raw or "[]")

    return [
        WorkflowRun(
            run_id=int(row["id"] if token else row["databaseId"]),
            head_sha=str(row["head_sha"] if token else row["headSha"]),
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
            latest = max(matches, key=lambda x: x.run_id)
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
    parser.add_argument("--repository", default="Pappu246/autonomous-ai-scout")
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

    require_auth(args.repository)
    head = remote_head(args.repository, args.branch)
    print(f"Verified remote head: {args.repository}:{args.branch} -> {head}")

    if args.apply_gate5 and not args.gate5:
        raise RuntimeError("--apply-gate5 requires --gate5")

    if args.check_only:
        for workflow in (GATE3, GATE4, GATE5):
            matches = [x for x in list_runs(workflow, args.branch, args.repository) if x.head_sha == head]
            print(f"{workflow}: exact-head-runs={len(matches)}")
            for item in sorted(matches, key=lambda x: x.run_id, reverse=True)[:3]:
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
