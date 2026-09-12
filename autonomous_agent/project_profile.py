from __future__ import annotations

import hashlib
import json
from typing import Any

PROFILE_FILES = (
    "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt", "pyproject.toml", "requirements.txt",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Dockerfile",
)


def gh_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Lazily delegate to the shared GitHub reader to avoid import cycles."""
    from .github_audit import gh_get as shared_gh_get
    return shared_gh_get(path, params)


def _root_entries(full_name: str) -> tuple[set[str], bool]:
    data = gh_get(f"/repos/{full_name}/contents/")
    if not isinstance(data, list):
        return set(), True
    return {str(item.get("name", "")) for item in data if item.get("name")}, False


def _languages(full_name: str) -> tuple[dict[str, int], bool]:
    data = gh_get(f"/repos/{full_name}/languages")
    if not isinstance(data, dict):
        return {}, True
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int) and v >= 0}, False


def build_project_profile(full_name: str, registry_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a bounded, read-only technical profile from repository metadata."""
    registry_profile = registry_profile or {}
    entries, contents_incomplete = _root_entries(full_name)
    languages, languages_incomplete = _languages(full_name)
    ecosystems: list[str] = []
    if {"pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock"} & entries:
        ecosystems.append("python")
    if "package.json" in entries:
        ecosystems.append("node")
    if "Cargo.toml" in entries:
        ecosystems.append("rust")
    if "go.mod" in entries:
        ecosystems.append("go")
    if {"pom.xml", "build.gradle"} & entries:
        ecosystems.append("jvm")
    if "Dockerfile" in entries:
        ecosystems.append("container")
    lockfiles = sorted(entries & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock"})
    ci_present = ".github" in entries
    license_present = any(name in entries for name in {"LICENSE", "LICENSE.md", "LICENSE.txt"}) or bool(registry_profile.get("license"))
    profile = {
        "full_name": full_name,
        "visibility": registry_profile.get("visibility", "unknown"),
        "private": bool(registry_profile.get("private", False)),
        "archived": bool(registry_profile.get("archived", False)),
        "fork": bool(registry_profile.get("fork", False)),
        "default_branch": registry_profile.get("default_branch", ""),
        "language": registry_profile.get("language"),
        "languages": languages,
        "root_files": sorted(name for name in entries if name in PROFILE_FILES),
        "ecosystems": ecosystems,
        "lockfiles": lockfiles,
        "has_readme": "README.md" in entries,
        "has_license": license_present,
        "has_ci_hint": ci_present,
        "incomplete": contents_incomplete or languages_incomplete,
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    profile["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return profile
