from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .action_queue import build_action_proposal, enqueue_proposal
from .benchmark import benchmark_gemini, benchmark_groq
from .dependency_security import analyze_dependencies
from .discovery import candidates_from_registry, discover_official_changes
from .github_audit import audit_owner
from .llm_planner import plan_with_free_llm
from .models import ProjectFinding, ScoutReport
from .opportunities import build_opportunities
from .patch_proposals import build_patch_proposal, save_proposal
from .project_intelligence import analyze_project
from .reporting import render_markdown
from .state import StateStore
from .task_engine import execute_task
from .verify import verify_candidate
from .emailer import send_report

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "providers.json"
REPORT_PATH = ROOT / "state" / "latest_report.md"
STATE_PATH = ROOT / "state" / "scout_state.json"
ACTION_QUEUE_PATH = ROOT / "state" / "approval_queue.json"
PATCH_PROPOSAL_PATH = ROOT / "state" / "patch_proposal.json"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _fingerprint(report: ScoutReport) -> str:
    payload = {
        "models": [m.model_dump(mode="json") for m in report.models],
        "findings": [f.model_dump(mode="json") for f in report.project_findings],
        "opportunities": [o.model_dump(mode="json") for o in report.opportunities],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def run() -> ScoutReport:
    registry = load_registry()
    store = StateStore(STATE_PATH)
    previous = store.load()

    task_request = os.getenv("TASK_REQUEST", "").strip()
    task_result = execute_task(task_request, ROOT) if task_request else None

    previous_sources = previous.get("source_hashes", {})
    candidates = candidates_from_registry(registry.get("providers", []))
    candidates = discover_official_changes(candidates, previous_sources)
    verified = [verify_candidate(c) for c in candidates]

    if os.getenv("ENABLE_FREE_BENCHMARKS", "false").lower() == "true":
        benchmarks = []
        for candidate in verified:
            if candidate.access_status.value != "verified_free" or candidate.model == "discovery-pending":
                continue
            if candidate.provider == "gemini":
                benchmarks.append(benchmark_gemini(candidate.model))
            elif candidate.provider == "groq":
                benchmarks.append(benchmark_groq(candidate.model))
        by_model = {(b.provider, b.model): b for b in benchmarks}
        for i, candidate in enumerate(verified):
            b = by_model.get((candidate.provider, candidate.model))
            if b:
                verified[i] = candidate.model_copy(update={"benchmark_latency_ms": b.latency_ms, "benchmark_ok": b.success})

    llm_plan = plan_with_free_llm(task_request, verified) if task_request else None
    action_proposal = None
    if task_request:
        if llm_plan:
            action_proposal = build_action_proposal(task_request, llm_plan.steps, llm_plan.requires_approval)
        else:
            action_proposal = build_action_proposal(task_request, tuple(task_result.plan.actions) if task_result else ())

    owner = os.getenv("SCOUT_OWNER", "Pappu246")
    exclude = {os.getenv("GITHUB_REPOSITORY", ""), "Pappu246/autonomous-ai-scout"}
    raw_findings = audit_owner(owner, exclude=exclude)
    findings = [ProjectFinding(repository=x.get("repository", ""), severity=x.get("severity", "info"), title=x["title"], detail=x["detail"], recommendation=x["recommendation"]) for x in raw_findings]
    findings.extend(analyze_project(ROOT, repository=os.getenv("GITHUB_REPOSITORY", "local")))
    findings.extend(analyze_dependencies(ROOT, repository=os.getenv("GITHUB_REPOSITORY", "local")))

    free_count = sum(1 for c in verified if c.access_status.value == "verified_free")
    opportunities = build_opportunities(owner, len({f.repository for f in findings}), sum(1 for f in findings if f.severity == "high"), free_count)
    report = ScoutReport(models=verified, project_findings=findings, opportunities=opportunities)
    fingerprint = _fingerprint(report)
    report.meaningful_change = fingerprint != previous.get("fingerprint", "")
    report.notes.append("Free-only policy is enforced. No paid billing, quota bypass, or production deployment is performed automatically.")
    report.notes.append("Benchmarks are opt-in with ENABLE_FREE_BENCHMARKS=true; missing keys or disabled benchmarking never trigger paid fallback.")
    report.notes.append("Project intelligence performs read-only dependency, secret-pattern, test, and license checks; it never modifies source files.")
    report.notes.append("Dependency security analysis is deterministic and offline; it flags reproducibility and install-hook risks without changing dependencies.")
    if task_result:
        report.notes.append(f"Task intent: {task_result.plan.intent.value}; status: {task_result.status}; risk: {task_result.plan.risk}.")
        report.notes.append(f"Task plan: {task_result.plan.explanation}")
        if task_result.status == "approval_required":
            report.notes.append("The requested task reached the write boundary; no source modification, merge, or deployment was performed without approval.")
    if llm_plan:
        report.notes.append(f"LLM plan selected {llm_plan.provider}/{llm_plan.model}: {llm_plan.summary}")
        report.notes.append("LLM-proposed steps: " + " | ".join(llm_plan.steps))
    elif task_request:
        report.notes.append("No optional free LLM plan was produced; deterministic bounded task planning remains the fallback and no paid model is used.")
    if action_proposal:
        report.notes.append(f"Action boundary: status={action_proposal.status.value}; approval_required={action_proposal.requires_approval}; reason={action_proposal.reason}")
        queued = enqueue_proposal(ACTION_QUEUE_PATH, action_proposal, task_result.plan.risk if task_result else "medium")
        if queued:
            report.notes.append(f"Approval queue: pending action {queued.id} recorded; execution remains blocked until an explicit approval flow is added.")
        if action_proposal.requires_approval:
            proposal_steps = llm_plan.steps if llm_plan else tuple(task_result.plan.actions) if task_result else ()
            patch = build_patch_proposal(task_request, proposal_steps, llm_plan.summary if llm_plan else "Sandboxed proposal generated from deterministic planning.")
            save_proposal(PATCH_PROPOSAL_PATH, patch)
            report.notes.append(f"Sandboxed patch proposal: {patch.id} saved for review; no source patch was generated or applied.")
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
        "fingerprint": _fingerprint(report),
        "source_hashes": {**previous.get("source_hashes", {}), **hashes},
        "verified_free_models": [f"{m.provider}/{m.model}" for m in report.models if m.access_status.value == "verified_free"],
        "report_path": str(REPORT_PATH),
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
        "last_task": os.getenv("TASK_REQUEST", "").strip(),
    })
    if report.meaningful_change and os.getenv("REPORT_EMAIL"):
        send_report("Autonomous AI Scout — meaningful update", rendered)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
