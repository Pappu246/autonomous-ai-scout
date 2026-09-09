from __future__ import annotations

import os
from typing import Any

import httpx


API = "https://api.github.com"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)


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


def audit_owner(owner: str, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    results: list[dict[str, Any]] = []
    for repo in inventory_repositories(owner):
        name = repo.get("full_name", "")
        if name in exclude:
            continue
        for finding in audit_repository(name):
            finding["repository"] = name
            results.append(finding)
    return results
