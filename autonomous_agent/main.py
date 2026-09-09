from __future__ import annotations

import json
import os
from pathlib import Path

from .benchmark import benchmark_gemini
from .discovery import candidates_from_registry, discover_official_changes
from .github_audit import audit_owner
from .models import ProjectFinding, ScoutReport
from .opportunities import build_opportunities
from .reporting import render_markdown
from .state import StateStore
from .verify import verify_candidate
from .emailer import send_report

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "providers.json"
REPORT_PATH = ROOT / "state" / "latest_report.md"
STATE_PATH = ROOT / "state" / "scout_state.json"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def run() -> ScoutReport:
    registry = load_registry()
    store = StateStore(STATE_PATH)
    previous = store.load()
    previous_sources = previous.get("source_hashes", {})
    candidates = candidates_from_registry(registry.get("providers", []))
    candidates = discover_official_changes(candidates, previous_sources)
    verified = [verify_candidate(c) for c in candidates]

    benchmarks = []
    for candidate in verified:
        if candidate.access_status.value == "verified_free" and candidate.provider == "gemini" and candidate.model != "discovery-pending":
            benchmarks.append(benchmark_gemini(candidate.model))
    by_model = {(b.provider, b.model): b for b in benchmarks}
    for i, candidate in enumerate(verified):
        b = by_model.get((candidate.provider, candidate.model))
        if b:
            verified[i] = candidate.model_copy(update={"benchmark_latency_ms": b.latency_ms, "benchmark_ok": b.success})

    owner = os.getenv("SCOUT_OWNER", "Pappu246")
    exclude = {os.getenv("GITHUB_REPOSITORY", ""), "Pappu246/autonomous-ai-scout"}
    raw_findings = audit_owner(owner, exclude=exclude)
    findings = [ProjectFinding(repository=x.get("repository", ""), severity=x.get("severity", "info"), title=x["title"], detail=x["detail"], recommendation=x["recommendation"]) for x in raw_findings]
    free_count = sum(1 for c in verified if c.access_status.value == "verified_free")
    opportunities = build_opportunities(owner, len({f.repository for f in findings}), sum(1 for f in findings if f.severity == "high"), free_count)
    changed = bool([c for c in verified if c.source_changed]) or bool(findings) or bool(opportunities)
    report = ScoutReport(models=verified, project_findings=findings, opportunities=opportunities, meaningful_change=changed)
    report.notes.append("Free-only policy is enforced. No paid billing, quota bypass, or production deployment is performed automatically.")
    report.notes.append("Benchmarks run only when a provider key is explicitly configured; missing keys cause a skip, never a paid fallback.")
    return report


def main() -> int:
    report = run()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_markdown(report)
    REPORT_PATH.write_text(rendered, encoding="utf-8")
    previous = StateStore(STATE_PATH).load()
    hashes = {str(m.source_url): m.source_hash for m in report.models if m.source_hash}
    StateStore(STATE_PATH).save({
        "last_run": report.generated_at.isoformat(),
        "meaningful_change": report.meaningful_change,
        "source_hashes": {**previous.get("source_hashes", {}), **hashes},
        "verified_free_models": [f"{m.provider}/{m.model}" for m in report.models if m.access_status.value == "verified_free"],
        "report_path": str(REPORT_PATH),
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
    })
    if report.meaningful_change and os.getenv("REPORT_EMAIL"):
        send_report("Autonomous AI Scout — meaningful update", rendered)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
