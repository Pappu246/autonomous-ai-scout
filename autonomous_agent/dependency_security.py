from __future__ import annotations

import json
import re
from pathlib import Path

from .models import ProjectFinding


_VERSIONED_PYTHON = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^\]]+\])?\s*(?:==|~=|>=|<=|>|<)\s*\S+")
_VERSIONED_NODE = re.compile(r"^[~^<>=]|^\d")


def _finding(repository: str, severity: str, title: str, detail: str, recommendation: str) -> ProjectFinding:
    return ProjectFinding(
        repository=repository,
        severity=severity,
        title=title,
        detail=detail,
        recommendation=recommendation,
        confidence=0.9,
    )


def _python_unpinned(path: Path) -> bool:
    if not path.exists():
        return False
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-r ", "--", "git+", "http://", "https://")):
            continue
        if _VERSIONED_PYTHON.match(line):
            continue
        if re.match(r"^[A-Za-z0-9_.-]+(?:\[[^\]]+\])?$", line):
            return True
    return False


def analyze_dependencies(root: Path, repository: str = "local") -> list[ProjectFinding]:
    """Run conservative, offline dependency-hygiene checks without modifying the project."""
    findings: list[ProjectFinding] = []
    requirements = root / "requirements.txt"
    if _python_unpinned(requirements):
        findings.append(_finding(
            repository,
            "low",
            "Python dependencies are not fully version-constrained",
            "requirements.txt contains at least one dependency without a version constraint.",
            "Pin or otherwise constrain production dependencies and keep a reproducible lock strategy.",
        ))

    package_json = root / "package.json"
    if package_json.exists():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            package = None
        if isinstance(package, dict):
            deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            unconstrained = [name for name, spec in deps.items() if isinstance(spec, str) and not _VERSIONED_NODE.match(spec)]
            if unconstrained:
                findings.append(_finding(
                    repository,
                    "low",
                    "Node dependencies use non-versioned sources",
                    "Dependency specs without a normal version/range were found: " + ", ".join(sorted(unconstrained)[:8]),
                    "Prefer explicit registry versions/ranges and review git/file dependencies before production use.",
                ))

            scripts = package.get("scripts", {})
            risky_hooks = [name for name in ("preinstall", "install", "postinstall") if name in scripts]
            if risky_hooks:
                findings.append(_finding(
                    repository,
                    "medium",
                    "Node install lifecycle scripts present",
                    "package.json defines install lifecycle hooks: " + ", ".join(risky_hooks) + ".",
                    "Review lifecycle scripts because dependency installation can execute arbitrary project commands.",
                ))

    lock_names = ("poetry.lock", "uv.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
    has_manifest = any((root / name).exists() for name in ("pyproject.toml", "requirements.txt", "Pipfile", "package.json"))
    has_lock = any((root / name).exists() for name in lock_names)
    if has_manifest and not has_lock:
        findings.append(_finding(
            repository,
            "low",
            "No dependency lockfile detected",
            "A supported dependency manifest exists but no common lockfile was found.",
            "Commit a lockfile when the package manager supports one to improve reproducibility and reviewability.",
        ))
    return findings
