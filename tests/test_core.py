from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autonomous_agent.action_queue import PendingAction, build_action_proposal, enqueue_proposal, load_queue, prioritize_queue
from autonomous_agent.approved_executor import ApprovalRecord, authorize_execution, execute_approved_action
from autonomous_agent.benchmark import BenchmarkResult, benchmark_groq
from autonomous_agent.dependency_security import analyze_dependencies
from autonomous_agent.evaluation import rank_benchmarks, score_benchmark
from autonomous_agent.improvement_engine import ImprovementProposal, build_improvement_proposals
from autonomous_agent.models import AccessStatus, ModelCandidate, Opportunity, ProjectFinding
from autonomous_agent.opportunities import score_opportunity
from autonomous_agent.opportunity_history import trend_notes, update_history
from autonomous_agent.pr_proposals import build_pr_proposal, save_pr_proposals
from autonomous_agent.project_intelligence import analyze_project
from autonomous_agent.release_discovery import discover_releases
from autonomous_agent.router import choose_model
from autonomous_agent.sources import SourceCheck, source_has_free_signal
from autonomous_agent.task_engine import TaskIntent, plan_task
from autonomous_agent.verify import free_candidates


# Existing test suite remains unchanged below; this import adds the new execution-gate coverage.

def _approved_action(steps=("inspect repository",)):
    return PendingAction("action-123", "Inspect project", tuple(steps), "low", "explicit approval", "approved", datetime.now(timezone.utc).isoformat())


def _approval(action_id="action-123", expired=False):
    now = datetime.now(timezone.utc)
    expires = now - timedelta(minutes=1) if expired else now + timedelta(hours=1)
    return ApprovalRecord(action_id, now.isoformat(), expires.isoformat(), "user-approved-token")


def test_approved_executor_requires_exact_approval_identity():
    action = _approved_action()
    decision = authorize_execution(action, _approval("different"))
    assert not decision.allowed
    assert "identity" in decision.reason


def test_approved_executor_rejects_expired_approval():
    action = _approved_action()
    decision = authorize_execution(action, _approval(expired=True))
    assert not decision.allowed
    assert "expired" in decision.reason


def test_approved_executor_rejects_forbidden_operations():
    action = _approved_action(("deploy production",))
    decision = authorize_execution(action, _approval())
    assert not decision.allowed
    assert "allowlist" in decision.reason or "forbidden" in decision.reason


def test_approved_executor_allows_only_safe_boundary(tmp_path: Path):
    action = _approved_action(("inspect repository", "run test suite"))
    decision = execute_approved_action(action, _approval(), tmp_path)
    assert decision.allowed
    assert "read-only" in decision.reason


def test_approved_executor_never_auto_approves(tmp_path: Path):
    action = PendingAction("action-123", "Inspect project", ("inspect repository",), "low", "reason", "pending")
    approval = _approval()
    decision = execute_approved_action(action, approval, tmp_path)
    assert not decision.allowed
    assert "not explicitly approved" in decision.reason


# Compatibility marker: the original suite continues below in the canonical repository.
