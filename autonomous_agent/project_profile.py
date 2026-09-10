from __future__ import annotations

import hashlib
import json
from typing import Any

from .github_audit import gh_get

PROFILE_FILES = (
    "README.md",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
)


def _root_file_names(full_name: str) -> set[str]:
    data = gh_get(f"/repos/{full_name}/contents/")
    if not isinstance(data, list):
        return set()
    return {str(item.get("name", "")) for item in data if item.get("type") == "file"}


def _languages(full_name: str) -> dict[str, int]:
    data = gh_get(f"/repos/{full_name}/languages")
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int) and v >= 0}


def build_project_profile(full_name: str, registry_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a bounded, read-only technical profile from repository metadata."""
    registry_profile = registry_profile or {}
    files = _root_file_names(full_name)
    languages = _languages(full_name)

    ecosystems: list[str] = []
    if {"pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock"} & files:
        ecosystems.append("python")
    if "package.json" in files:
        ecosystems.append("node")
    if "Cargo.toml" in files:
        ecosystems.append("rust")
    if "go.mod" in files:
        ecosystems.append("go")
    if {"pom.xml", "build.gradle"} & files:
        ecosystems.append("jvm")
    if "Dockerfile" in files:
        ecosystems.append("container")

    lockfiles = sorted(files & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock"})
    ci_present = any(name.startswith(".github") for name in files)
    license_present = any(name in files for name in {"LICENSE", "LICENSE.md", "LICENSE.txt"}) or bool(registry_profile.get("license"))

    profile = {
        "full_name": full_name,
        "visibility": registry_profile.get("visibility", "unknown"),
        "private": bool(registry_profile.get("private", False)),
        "archived": bool(registry_profile.get("archived", False)),
        "fork": bool(registry_profile.get("fork", False)),
        "default_branch": registry_profile.get("default_branch", ""),
        "language": registry_profile.get("language"),
        "languages": languages,
        "root_files": sorted(name for name in files if name in PROFILE_FILES),
        "ecosystems": ecosystems,
        "lockfiles": lockfiles,
        "has_readme": "README.md" in files,
        "has_license": license_present,
        "has_ci_hint": ci_present,
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    profile["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return profile
