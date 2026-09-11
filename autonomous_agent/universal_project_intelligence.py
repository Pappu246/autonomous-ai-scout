from __future__ import annotations

from typing import Any

DEPENDENCY_ECOSYSTEMS = {"python", "node", "rust", "go", "jvm"}


def build_universal_intelligence(profile: dict[str, Any]) -> dict[str, Any]:
    """Derive deterministic, read-only project health signals from a technical profile."""
    ecosystems = set(profile.get("ecosystems", []))
    lockfiles = set(profile.get("lockfiles", []))
    dependency_ecosystems = ecosystems & DEPENDENCY_ECOSYSTEMS

    signals: list[str] = []
    priorities: list[str] = []
    if dependency_ecosystems and not lockfiles:
        signals.append("dependencies_without_lockfile")
        priorities.append("reproducibility")
    if not profile.get("has_readme"):
        signals.append("missing_readme")
        priorities.append("documentation")
    if not profile.get("has_license") and not profile.get("private"):
        signals.append("missing_license")
        priorities.append("licensing")
    if not profile.get("has_ci_hint"):
        signals.append("missing_ci_hint")
        priorities.append("validation")
    if profile.get("archived"):
        signals.append("archived_project")
        priorities.append("lifecycle")

    if "node" in ecosystems and not lockfiles:
        evidence = "node_dependencies_unpinned"
    elif dependency_ecosystems and not lockfiles:
        evidence = "dependency_reproducibility_gap"
    else:
        evidence = "profile_baseline"

    score = max(0, 100 - 15 * len(signals))
    return {
        "full_name": profile.get("full_name", ""),
        "score": score,
        "signals": signals,
        "priorities": sorted(set(priorities)),
        "evidence": evidence,
        "fingerprint": profile.get("fingerprint", ""),
    }
