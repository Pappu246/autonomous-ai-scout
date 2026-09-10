from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .project_registry import build_project_registry


API = "https://api.github.com"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
PROJECT_REGISTRY_PATH = Path(os.getenv("SCOUT_PROJECT_REGISTRY_PATH", "state/project_registry.json"))


def _headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "autonomous-ai-scout/0.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def gh_get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        r = httpx.get(API + path, headers=_headers(), params=params, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def inventory_repositories(owner: str) -> list[dict[str, Any]]:
    data = gh_get(f"/users/{owner}/repos", {"per_page": 100, "sort": "updated"}) or []
    if not isinstance(data, list):
        return []
    return [r for r in data if not r.get("fork") and not r.get("archived")]


def audit_repository(full_name: str) -> list[dict[str, Any]]:
    repo = gh_get(f"/repos/{full_name}") or {}
    if not repo:
        return [{"severity": "warning", "title": "Repository could not be read", "detail": full_name, "recommendation": "Check repository visibility/token access."}]
    findings: list[dict[str, Any]] = []
    if not repo.get("has_issues"):
        findings.append({"severity": "info", "title": "GitHub Issues disabled", "detail": "Issue tracking is disabled for this repository.", "recommendation": "Enable Issues if this project benefits from structured engineering tasks."})
    if not repo.get("has_wiki"):
        findings.append({"severity": "info", "title": "Wiki disabled", "detail": "The repository has no GitHub Wiki enabled.", "recommendation": "Keep disabled unless project documentation needs a separate wiki."})
    if not repo.get("license"):
        findings.append({"severity": "medium", "title": "No detected license", "detail": "GitHub does not detect a repository license.", "recommendation": "Add an explicit license if the project is intended for public reuse."})
    if repo.get("open_issues_count", 0) > 10:
        findings.append({"severity": "medium", "title": "Large open-issue backlog", "detail": f"{repo.get('open_issues_count')} open issues are visible.", "recommendation": "Prioritize, label, close stale items, and turn high-value issues into milestones."})
    if repo.get("default_branch"):
        branch = gh_get(f"/repos/{full_name}/branches/{repo['default_branch']}") or {}
        if branch and not branch.get("protected"):
            findings.append({"severity": "low", "title": "Default branch is not protected", "detail": f"{repo['default_branch']} is not reported as protected.", "recommendation": "Consider branch protection and required CI checks before production work."})
    issues = gh_get(f"/repos/{full_name}/issues", {"state": "open", "per_page": 10, "sort": "updated"}) or []
    if isinstance(issues, list):
        bug_count = sum(1 for i in issues if "bug" in " ".join(i.get("labels", []) and [x.get("name", "") for x in i.get("labels", [])]).lower())
        if bug_count:
            findings.append({"severity": "high", "title": "Open bug issues detected", "detail": f"At least {bug_count} recent open issue(s) are labelled as bugs.", "recommendation": "Review the highest-impact bug first and add regression tests before changes."})
    return findings


def _load_project_registry() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(PROJECT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_project_registry(projects: dict[str, dict[str, Any]]) -> None:
    PROJECT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROJECT_REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(projects, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(PROJECT_REGISTRY_PATH)


def audit_owner(owner: str, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    results: list[dict[str, Any]] = []

    previous_registry = _load_project_registry()
    projects, changes = build_project_registry(owner, previous_registry)
    _save_project_registry(projects)

    for name in changes["new"]:
        results.append({
            "repository": name,
            "severity": "info",
            "title": "New GitHub repository discovered",
            "detail": "The project registry detected a repository not present in the previous persisted baseline.",
            "recommendation": "Classify and establish a health baseline during the next project-intelligence cycle.",
        })
    for name in changes["changed"]:
        results.append({
            "repository": name,
            "severity": "info",
            "title": "GitHub repository metadata changed",
            "detail": "Tracked repository metadata differs from the previous persisted baseline.",
            "recommendation": "Re-run project-specific intelligence and compare health/security signals.",
        })
    for name in changes["removed"]:
        results.append({
            "repository": name,
            "severity": "warning",
            "title": "GitHub repository no longer discovered",
            "detail": "The repository existed in the previous project registry but is not present now.",
            "recommendation": "Confirm whether it was deleted, transferred, or access was revoked before taking action.",
        })
    for name, profile in sorted(projects.items()):
        if profile.get("archived"):
            results.append({
                "repository": name,
                "severity": "info",
                "title": "Repository is archived",
                "detail": "GitHub marks this repository as archived.",
                "recommendation": "Exclude it from active improvement work unless explicitly reactivated.",
            })

    # Use the authoritative project registry for deep audits. This means authorized
    # private repositories discovered through /user/repos are not silently skipped.
    for name, profile in sorted(projects.items()):
        if name in exclude or profile.get("fork") or profile.get("archived"):
            continue
        for finding in audit_repository(name):
            finding["repository"] = name
            results.append(finding)
    return results
