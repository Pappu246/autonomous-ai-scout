#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request


GATE_WORKFLOWS = (
    ("gate3-coding-provider-smoke.yml", "Gate 3"),
    ("gate4-external-connector-smoke.yml", "Gate 4"),
    ("gate5-github-admin-readiness.yml", "Gate 5"),
)
GATE5_APPLY_STEP = "Apply hardened main-branch protection (apply_protection=true)"


def api(method: str, path: str, *, repository: str, token: str, body=None):
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "autonomous-ai-scout-formal-release",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"GitHub API {method} {path} failed with HTTP {exc.code}: {detail}") from exc


def successful_exact_run(workflow: str, repository: str, sha: str, token: str) -> dict:
    owner, name = repository.split("/", 1)
    data = api(
        "GET",
        f"/repos/{owner}/{name}/actions/workflows/{workflow}/runs?head_sha={sha}&status=completed&per_page=20",
        repository=repository,
        token=token,
    ) or {}
    good = [
        run for run in data.get("workflow_runs", [])
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if not good:
        raise RuntimeError(f"{workflow} has no successful completed run for exact release SHA {sha}.")
    return good[0]


def verify_gate5_application(repository: str, sha: str, token: str) -> int:
    run = successful_exact_run("gate5-github-admin-readiness.yml", repository, sha, token)
    owner, name = repository.split("/", 1)
    jobs = api(
        "GET",
        f"/repos/{owner}/{name}/actions/runs/{run['id']}/jobs?per_page=100",
        repository=repository,
        token=token,
    ) or {}
    for job in jobs.get("jobs", []):
        for step in job.get("steps", []):
            if step.get("name") == GATE5_APPLY_STEP and step.get("conclusion") == "success":
                return int(run["id"])
    raise RuntimeError("Gate 5 has no successful protection-application step for the exact release SHA.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for formal release publication.")
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise RuntimeError("release SHA must be a full 40-character lowercase commit SHA.")
    if not re.fullmatch(r"v0\.[0-9]+\.[0-9]+", args.tag):
        raise RuntimeError(f"invalid release tag format: {args.tag}")

    owner, name = args.repository.split("/", 1)
    comparison = api(
        "GET",
        f"/repos/{owner}/{name}/compare/{args.sha}...main",
        repository=args.repository,
        token=token,
    ) or {}
    if comparison.get("status") not in {"ahead", "identical"}:
        raise RuntimeError(
            f"release SHA {args.sha} is not an ancestor of the current main branch."
        )

    version_file = api(
        "GET",
        f"/repos/{owner}/{name}/contents/pyproject.toml?ref={args.sha}",
        repository=args.repository,
        token=token,
    ) or {}
    try:
        version_text = base64.b64decode(version_file["content"]).decode("utf-8")
        version = tomllib.loads(version_text)["project"]["version"]
    except (KeyError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(
            "could not read project version from pyproject.toml at the exact release SHA."
        ) from exc
    expected = f"v{version}"
    if args.tag != expected:
        raise RuntimeError(f"tag {args.tag} does not match project version {expected}")

    owner, name = args.repository.split("/", 1)
    check_runs = api(
        "GET",
        f"/repos/{owner}/{name}/commits/{args.sha}/check-runs?per_page=100",
        repository=args.repository,
        token=token,
    ) or {}
    tests = [x for x in check_runs.get("check_runs", []) if x.get("name") == "test"]
    latest = max(tests, key=lambda x: x.get("completed_at") or "") if tests else None
    if not latest or latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise RuntimeError("Exact-SHA CI test check is not green.")

    verified = {}
    for workflow, label in GATE_WORKFLOWS:
        verified[label] = successful_exact_run(workflow, args.repository, args.sha, token)["id"]
    verified["Gate 5 application"] = verify_gate5_application(args.repository, args.sha, token)

    if api(
        "GET",
        f"/repos/{owner}/{name}/git/ref/tags/{args.tag}",
        repository=args.repository,
        token=token,
    ) is not None:
        raise RuntimeError(f"Release tag already exists: {args.tag}")

    body = (
        "Formal release of the verified Autonomous AI Scout readiness baseline.\n\n"
        "Safety boundary: publication required explicit confirmation, the exact verified release SHA, "
        "green CI, successful Gate 3/4/5 evidence, and Gate 5 protection-application evidence."
    )
    release = api(
        "POST",
        f"/repos/{owner}/{name}/releases",
        repository=args.repository,
        token=token,
        body={
            "tag_name": args.tag,
            "target_commitish": args.sha,
            "name": args.title,
            "body": body,
            "draft": False,
            "prerelease": False,
            "generate_release_notes": False,
        },
    )
    print(json.dumps({"release": release, "verified": verified}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        raise SystemExit(2)
