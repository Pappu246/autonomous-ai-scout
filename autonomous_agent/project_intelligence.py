from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .cross_project_memory import CrossProjectMemory
from .models import ProjectFinding


SECRET_PATTERNS = (
    re.compile(r"(?:api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]{12,}['\"]", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _finding(repository: str, severity: str, title: str, detail: str, recommendation: str) -> ProjectFinding:
    return ProjectFinding(repository=repository, severity=severity, title=title, detail=detail, recommendation=recommendation)


def _memory_record(memory: CrossProjectMemory | None, operation: str, callback) -> None:
    if memory is None:
        return
    try:
        callback()
    except Exception:
        # Intelligence must remain usable if observational persistence is unavailable.
        return


def analyze_project(root: Path, repository: str = "local", *, memory: CrossProjectMemory | None = None) -> list[ProjectFinding]:
    """Perform deterministic checks and optionally persist observational evidence."""
    findings: list[ProjectFinding] = []

    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"
    package_json = root / "package.json"
    lock_files = [root / name for name in ("poetry.lock", "uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")]

    if pyproject.exists() or requirements.exists():
        dep_source = pyproject if pyproject.exists() else requirements
        text = dep_source.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            findings.append(_finding(repository, "medium", "Empty Python dependency manifest", f"{dep_source.name} exists but contains no dependency information.", "Declare runtime dependencies explicitly."))

    if package_json.exists():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            if deps and not any(path.exists() for path in lock_files):
                findings.append(_finding(repository, "medium", "Node project has no lockfile", "package.json declares dependencies but no common lockfile was found.", "Commit the package-manager lockfile for reproducible installs."))
        except json.JSONDecodeError:
            findings.append(_finding(repository, "high", "Invalid package.json", "package.json is not valid JSON.", "Fix package.json before relying on automated builds."))

    tracked_text_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml", ".toml", ".env"}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000:
            continue
        if path.suffix.lower() not in tracked_text_extensions and path.name != ".env":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS) and path.name not in {".env.example", ".env.sample"}:
            findings.append(_finding(repository, "high", "Possible hard-coded secret", f"A secret-like assignment was detected in {path.relative_to(root)}.", "Move credentials to environment/secret storage and rotate exposed credentials."))
            break

    source_files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and p.suffix in {".py", ".js", ".ts", ".tsx", ".jsx"}]
    if source_files and not (root / "tests").exists() and not (root / "test").exists() and not (root / "__tests__").exists():
        findings.append(_finding(repository, "medium", "No test directory detected", "Source files exist but no conventional test directory was found.", "Add focused automated tests for critical behavior."))

    if not (root / "LICENSE").exists() and not (root / "LICENSE.md").exists() and not (root / "LICENSE.txt").exists():
        findings.append(_finding(repository, "low", "License file missing", "No common top-level LICENSE file was detected.", "Add an explicit open-source license if the project is intended to be public."))

    if memory is not None:
        for finding in findings:
            _memory_record(memory, "finding", lambda finding=finding: memory.record_finding(repository, finding.title, severity=finding.severity))
            _memory_record(memory, "recommendation", lambda finding=finding: memory.record_recommendation(repository, finding.recommendation, status="observed"))
        severity_counts = {level: sum(item.severity == level for item in findings) for level in ("high", "medium", "low")}
        baseline = {"finding_count": len(findings), "severity_counts": severity_counts}
        _memory_record(memory, "baseline", lambda: memory.record_health_baseline(repository, baseline))
        fingerprint_payload = [(item.title, item.severity, item.recommendation) for item in findings]
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if memory.change_detected(repository, "project_intelligence", fingerprint):
            _memory_record(memory, "change", lambda: memory.record_change(repository, "project_intelligence", fingerprint))

    return findings
