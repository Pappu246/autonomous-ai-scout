from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ai_coding_brain import AICodingBrain, GitRepositoryInspector, RepositoryContext
from .continuous_improvement import ImprovementProposal, build_proposal
from .models import ProjectFinding
from .provider_router import build_provider_router_from_env
from .sandbox_runner import LocalSandboxTestRunner
from .self_improvement import ImprovementRun, approval_payload


class LocalRepositoryReader:
    """Read only the requested files from a local checkout."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def __call__(self, repository: str, path: str) -> str:
        del repository
        target = (self.root / path).resolve()
        if self.root not in target.parents and target != self.root:
            raise ValueError("repository context path escapes the local workspace")
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8")


@dataclass(frozen=True)
class LocalCodingPipeline:
    root: Path
    brain: AICodingBrain

    def run(self, proposal: ImprovementProposal) -> ImprovementRun:
        return self.brain.run(proposal)

    def run_finding(
        self,
        *,
        project: str,
        severity: str,
        title: str,
        detail: str,
        recommendation: str,
        affected_area: tuple[str, ...] = (),
        confidence: float = 0.8,
    ) -> ImprovementRun:
        finding = ProjectFinding(
            repository=project,
            severity=severity,
            title=title,
            detail=detail,
            recommendation=recommendation,
            confidence=confidence,
        )
        proposal = build_proposal(project, finding)
        if affected_area:
            proposal = type(proposal)(
                **{
                    **proposal.__dict__,
                    "affected_area": tuple(affected_area),
                }
            )
        return self.run(proposal)


def build_local_coding_pipeline(
    root: str | Path,
    *,
    allow_paid: bool = False,
    timeout_seconds: int = 120,
    max_revisions: int = 2,
) -> LocalCodingPipeline:
    workspace = Path(root).resolve()
    if not workspace.is_dir():
        raise ValueError("coding workspace must be an existing directory")
    router = build_provider_router_from_env(allow_paid=allow_paid)
    inspector = GitRepositoryInspector(LocalRepositoryReader(workspace))
    runner = LocalSandboxTestRunner(workspace, timeout_seconds=timeout_seconds)
    brain = AICodingBrain(
        model=router,
        inspector=inspector,
        validator=runner,
        max_revisions=max_revisions,
    )
    return LocalCodingPipeline(workspace, brain)


def summarize_approval(run: ImprovementRun) -> dict[str, object]:
    """Return the bounded, secret-redacted payload that can be shown to a human."""
    return approval_payload(run)
