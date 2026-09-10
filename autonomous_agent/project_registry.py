from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

API = "https://api.github.com"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_REPOSITORIES = 5000


def _headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "autonomous-ai-scout/project-registry",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def gh_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Read GitHub metadata without raising into the scheduled Scout loop."""
    try:
        response = httpx.get(API + path, headers=_headers(), params=params, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def _max_repositories() -> int:
    raw = os.getenv("SCOUT_MAX_REPOSITORIES", str(DEFAULT_MAX_REPOSITORIES)).strip()
    try:
        return max(1, min(int(raw), DEFAULT_MAX_REPOSITORIES))
    except ValueError:
        return DEFAULT_MAX_REPOSITORIES


def _owned_repository_page(owner: str, page: int) -> list[dict[str, Any]] | None:
    # Authenticated /user/repos is required for authorized private repositories.
    # affiliation=owner prevents collaborator/org repositories from entering the registry.
    if os.getenv("GITHUB_TOKEN", "").strip():
        data = gh_get("/user/repos", {"affiliation": "owner", "per_page": DEFAULT_PAGE_SIZE, "page": page, "sort": "updated"})
    else:
        data = gh_get(f"/users/{owner}/repos", {"per_page": DEFAULT_PAGE_SIZE, "page": page, "sort": "updated"})
    if data is None:
        return None
    return data if isinstance(data, list) else None


def discover_repositories(owner: str) -> list[dict[str, Any]] | None:
    """Discover all repositories owned by the configured account, including archived/forks.

    None means discovery was unavailable/incomplete; an empty list means a successful
    discovery returned no repositories. This distinction prevents transient API failures
    from being misclassified as mass repository deletion.
    """
    repositories: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_repositories = _max_repositories()

    for page in range(1, (max_repositories + DEFAULT_PAGE_SIZE - 1) // DEFAULT_PAGE_SIZE + 1):
        batch = _owned_repository_page(owner, page)
        if batch is None:
            return None
        if not batch:
            break
        for repo in batch:
            full_name = str(repo.get("full_name", "")).strip()
            if not full_name or full_name in seen:
                continue
            if str(repo.get("owner", {}).get("login", owner)).lower() != owner.lower():
                continue
            seen.add(full_name)
            repositories.append(repo)
            if len(repositories) >= max_repositories:
                return repositories
        if len(batch) < DEFAULT_PAGE_SIZE:
            break
    return repositories


def _profile(repo: dict[str, Any]) -> dict[str, Any]:
    owner = repo.get("owner") or {}
    profile = {
        "id": repo.get("id"),
        "full_name": repo.get("full_name", ""),
        "name": repo.get("name", ""),
        "owner": owner.get("login", ""),
        "visibility": repo.get("visibility", "public"),
        "private": bool(repo.get("private", False)),
        "archived": bool(repo.get("archived", False)),
        "fork": bool(repo.get("fork", False)),
        "default_branch": repo.get("default_branch", ""),
        "language": repo.get("language"),
        "description": repo.get("description") or "",
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "open_issues_count": int(repo.get("open_issues_count") or 0),
        "html_url": repo.get("html_url", ""),
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    profile["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return profile


def build_project_registry(owner: str, previous: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Return current project profiles plus deterministic new/changed/removed sets."""
    previous = previous or {}
    discovered = discover_repositories(owner)
    if discovered is None:
        # Preserve the last known-good baseline on transient discovery failure.
        return dict(previous), {"new": [], "changed": [], "removed": []}

    current: dict[str, dict[str, Any]] = {}
    for repo in discovered:
        profile = _profile(repo)
        current[profile["full_name"]] = profile

    new = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    changed = sorted(
        name
        for name in set(current) & set(previous)
        if current[name].get("fingerprint") != previous[name].get("fingerprint")
    )
    return current, {"new": new, "changed": changed, "removed": removed}
