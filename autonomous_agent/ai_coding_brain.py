from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from .continuous_improvement import ImprovementProposal
from .self_improvement import (
    ImprovementRun,
    PatchCandidate,
    PatchGenerator,
    PatchValidator,
    SelfImprovementLoop,
    ValidationResult,
)

_MAX_CONTEXT_FILES = 30
_MAX_FILE_BYTES = 40_000
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----",
    re.S,
)


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    content: str


@dataclass(frozen=True)
class RepositoryContext:
    repository: str
    files: tuple[RepositoryFile, ...]
    truncated: bool = False


class RepositoryInspector(Protocol):
    def inspect(self, repository: str, paths: Iterable[str]) -> RepositoryContext: ...


class CodingModel(Protocol):
    def generate_patch(
        self,
        *,
        proposal: ImprovementProposal,
        context: RepositoryContext,
        feedback: str = "",
        previous: PatchCandidate | None = None,
    ) -> PatchCandidate | None: ...


class TestRunner(Protocol):
    def validate(
        self,
        proposal: ImprovementProposal,
        candidate: PatchCandidate,
        *,
        context: RepositoryContext,
    ) -> ValidationResult: ...


def _redact(text: str) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    return _SECRET.sub("[REDACTED]", text)


def _bounded(text: str) -> str:
    return _redact(text)[:_MAX_FILE_BYTES]


class GitRepositoryInspector:
    """Read-only repository context adapter.

    The adapter is intentionally storage-agnostic: GitHub, a local checkout,
    or another repository backend can implement the same inspect contract.
    """

    def __init__(self, reader):
        self.reader = reader

    def inspect(self, repository: str, paths: Iterable[str]) -> RepositoryContext:
        requested_paths = tuple(dict.fromkeys(paths))
        files: list[RepositoryFile] = []
        truncated = False

        for path in requested_paths[:_MAX_CONTEXT_FILES]:
            try:
                content = self.reader(repository, path)
            except Exception:
                continue
            if not isinstance(content, str):
                content = str(content)
            bounded = _bounded(content)
            truncated = truncated or len(bounded) < len(content)
            files.append(RepositoryFile(path=path, content=bounded))

        if len(requested_paths) > _MAX_CONTEXT_FILES:
            truncated = True

        return RepositoryContext(repository, tuple(files), truncated)


class ModelPatchGenerator(PatchGenerator):
    def __init__(self, model: CodingModel, inspector: RepositoryInspector):
        self.model = model
        self.inspector = inspector

    def generate(self, proposal, *, feedback="", previous=None):
        paths = proposal.affected_area or proposal.impact_analysis.regression_surface
        context = self.inspector.inspect(proposal.project, paths)
        return self.model.generate_patch(
            proposal=proposal,
            context=context,
            feedback=feedback,
            previous=previous,
        )


class ContextualPatchValidator(PatchValidator):
    def __init__(self, runner: TestRunner, inspector: RepositoryInspector):
        self.runner = runner
        self.inspector = inspector

    def validate(self, proposal, candidate, review):
        paths = tuple(review.files)
        context = self.inspector.inspect(proposal.project, paths)
        return self.runner.validate(proposal, candidate, context=context)


class AICodingBrain:
    """Plan and generate a reviewable patch, then validate it in a bounded loop.

    The brain does not mutate source control. A successful run ends at
    READY_FOR_APPROVAL and can then be handed to the N12 GitHub worker.
    """

    def __init__(
        self,
        *,
        model: CodingModel,
        inspector: RepositoryInspector,
        validator: TestRunner,
        max_revisions: int = 2,
    ):
        self.loop = SelfImprovementLoop(max_revisions=max_revisions)
        self.generator = ModelPatchGenerator(model, inspector)
        self.validator = ContextualPatchValidator(validator, inspector)

    def run(self, proposal: ImprovementProposal) -> ImprovementRun:
        return self.loop.run(
            proposal,
            generator=self.generator,
            validator=self.validator,
        )


def build_model_prompt(
    proposal: ImprovementProposal,
    context: RepositoryContext,
    *,
    feedback: str = "",
) -> str:
    """Create a bounded coding prompt with explicit non-mutation constraints."""
    payload = {
        "task": proposal.problem,
        "solution": proposal.proposed_solution,
        "validation": proposal.validation_strategy,
        "affected_area": proposal.affected_area,
        "repository": context.repository,
        "files": [{"path": item.path, "content": item.content} for item in context.files],
        "feedback": _bounded(feedback),
        "constraints": [
            "Return a reviewable patch candidate only.",
            "Do not expose, invent, or request secrets.",
            "Do not modify CI workflow, .env, .git, or secret paths.",
            "Do not merge, deploy, bill, or perform external side effects.",
            "The changed-file manifest must exactly match the unified diff.",
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)
