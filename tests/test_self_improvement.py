from __future__ import annotations

from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.models import ProjectFinding
from autonomous_agent.self_improvement import (
    ImprovementStatus,
    PatchCandidate,
    SelfImprovementLoop,
    ValidationResult,
    approval_payload,
)

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
"""
FILES = {"app.py": 'print("new")\n'}


def proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="CI regression",
            detail="A regression was detected in the test suite.",
            recommendation="Fix the regression and add focused coverage.",
            confidence=0.9,
        ),
    )


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, proposal, *, feedback="", previous=None):
        self.calls.append((feedback, previous))
        self.last_feedback = feedback
        return PatchCandidate(DIFF, FILES, "Fix regression", ("python -m pytest -q",))


class RevisingGenerator:
    def __init__(self):
        self.calls = 0
        self.last_feedback = ""

    def generate(self, proposal, *, feedback="", previous=None):
        self.calls += 1
        self.last_feedback = feedback
        if self.calls == 1:
            return PatchCandidate(DIFF, FILES, "First attempt")
        return PatchCandidate(DIFF, FILES, "Revised attempt", ("python -m pytest -q",))


class Validator:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def validate(self, proposal, candidate, review):
        self.calls += 1
        return self.outcomes.pop(0)


def test_success_stops_after_first_validated_candidate():
    generator = Generator()
    validator = Validator([ValidationResult(True, "tests passed")])

    result = SelfImprovementLoop(max_revisions=2).run(
        proposal(), generator=generator, validator=validator
    )

    assert result.status is ImprovementStatus.READY_FOR_APPROVAL
    assert len(result.attempts) == 1
    assert result.candidate is not None
    assert result.approval_required is True


def test_failed_validation_causes_bounded_revision():
    generator = RevisingGenerator()
    validator = Validator(
        [ValidationResult(False, "test_x failed"), ValidationResult(True, "fixed")]
    )

    result = SelfImprovementLoop(max_revisions=2).run(
        proposal(), generator=generator, validator=validator
    )

    assert result.status is ImprovementStatus.READY_FOR_APPROVAL
    assert len(result.attempts) == 2
    assert generator.calls == 2
    assert generator.last_feedback == "test_x failed"


def test_revision_budget_is_bounded():
    generator = RevisingGenerator()
    validator = Validator([ValidationResult(False, "still failing")] * 10)

    result = SelfImprovementLoop(max_revisions=2).run(
        proposal(), generator=generator, validator=validator
    )

    assert result.status is ImprovementStatus.VALIDATION_FAILED
    assert len(result.attempts) == 3
    assert generator.calls == 3


def test_invalid_patch_never_reaches_validator():
    class BadGenerator:
        def generate(self, proposal, *, feedback="", previous=None):
            return PatchCandidate(
                "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n+SECRET=x\n",
                {".env": "SECRET=x\n"},
                "unsafe",
            )

    validator = Validator([ValidationResult(True, "should not run")])
    result = SelfImprovementLoop(max_revisions=0).run(
        proposal(), generator=BadGenerator(), validator=validator
    )

    assert result.status is ImprovementStatus.VALIDATION_FAILED
    assert validator.calls == 0


def test_approval_payload_is_redacted_and_non_mutating():
    generator = Generator()
    validator = Validator([ValidationResult(True, "token=supersecret")])
    result = SelfImprovementLoop(max_revisions=0).run(
        proposal(), generator=generator, validator=validator
    )

    payload = approval_payload(result)
    assert "supersecret" not in str(payload)
    assert result.status is ImprovementStatus.READY_FOR_APPROVAL

def test_oversized_candidate_file_is_rejected_before_validation():
    class OversizedGenerator:
        def generate(self, proposal, *, feedback="", previous=None):
            return PatchCandidate(
                DIFF,
                {"app.py": "x" * 200_001},
                "oversized",
            )

    validator = Validator([ValidationResult(True, "should not run")])
    result = SelfImprovementLoop(max_revisions=0).run(
        proposal(), generator=OversizedGenerator(), validator=validator
    )

    assert result.status is ImprovementStatus.VALIDATION_FAILED
    assert validator.calls == 0
    assert "maximum size" in result.attempts[0].validation.detail
