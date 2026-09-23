from __future__ import annotations

from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.models import ProjectFinding
from autonomous_agent.self_improvement import ImprovementStatus, PatchCandidate, SelfImprovementLoop, ValidationResult
from autonomous_agent.patch_review import review_patch


DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
"""


def _proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="bug",
            detail="tests fail",
            recommendation="fix",
            confidence=0.9,
        ),
    )


class V:
    def validate(self, proposal, candidate, review):
        return ValidationResult(True, "ok")


def test_mismatched_file_contents_are_rejected():
    candidate = PatchCandidate(DIFF, {"app.py": 'print("attacker")\n'}, "bad", ())
    class G:
        def generate(self, proposal, *, feedback="", previous=None):
            return candidate
    result = SelfImprovementLoop(max_revisions=0).run(_proposal(), generator=G(), validator=V())
    assert result.status is ImprovementStatus.VALIDATION_FAILED
    assert "do not match the reviewed diff" in result.reason if result.reason else "mismatch" in result.attempts[0].validation.detail


def test_secret_bearing_file_contents_are_rejected():
    candidate = PatchCandidate(DIFF, {"app.py": 'API_KEY=supersecret\nprint("new")\n'}, "bad", ())
    class G:
        def generate(self, proposal, *, feedback="", previous=None):
            return candidate
    result = SelfImprovementLoop(max_revisions=0).run(_proposal(), generator=G(), validator=V())
    assert result.status is ImprovementStatus.VALIDATION_FAILED
    assert "sensitive" in result.attempts[0].validation.detail


def test_patch_without_changed_files_is_rejected():
    review = review_patch("some non-empty text without a unified file manifest")
    assert not review.allowed
    assert "changed-file manifest" in review.reason
