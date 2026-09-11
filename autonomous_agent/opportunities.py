from __future__ import annotations

from .models import Opportunity
from .opportunity_dedup import deduplicate_opportunities


def score_opportunity(repo_count: int, bug_findings: int, free_models: int) -> float:
    score = 25.0 + min(repo_count * 5, 20) + min(bug_findings * 3, 25) + min(free_models * 5, 30)
    return min(score, 100.0)


def build_opportunities(owner: str, repo_count: int, bug_findings: int, free_models: int) -> list[Opportunity]:
    score = score_opportunity(repo_count, bug_findings, free_models)
    result = [
        Opportunity(
            title="Turn repeated project maintenance into a service",
            description=f"The scout sees {repo_count} active public project(s) and {bug_findings} bug-related finding(s). A productized maintenance/audit service could turn existing engineering work into a portfolio offer.",
            score=score,
            next_step="Package a one-page offer: audit, prioritized fixes, tests, and monthly maintenance.",
        ),
        Opportunity(
            title="Build a free-model AI toolkit",
            description=f"{free_models} candidate free-access model(s) were verified or surfaced. A reusable router/benchmark toolkit can become an open-source portfolio project or SaaS prototype.",
            score=min(score + 5, 100),
            next_step="Publish benchmark results and document where each free model is legitimately usable.",
        ),
    ]
    return deduplicate_opportunities(result)
