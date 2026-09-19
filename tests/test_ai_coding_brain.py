from __future__ import annotations

from autonomous_agent.ai_coding_brain import (
    AICodingBrain,
    GitRepositoryInspector,
    RepositoryContext,
    RepositoryFile,
    build_model_prompt,
)
from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.models import ProjectFinding
from autonomous_agent.self_improvement import PatchCandidate, ValidationResult


DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
"""
FILES = {"app.py": 'print("new")\n'}


def make_proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="CI regression",
            detail="tests fail",
            recommendation="fix regression",
            confidence=0.9,
        ),
    )


class Model:
    def generate_patch(self, *, proposal, context, feedback="", previous=None):
        assert context.files
        return PatchCandidate(DIFF, FILES, "fix regression", ("pytest -q",))


class Runner:
    def validate(self, proposal, candidate, *, context):
        assert context.files[0].path == "app.py"
        return ValidationResult(True, "tests passed")


def test_brain_reaches_approval_without_mutating_source():
    brain = AICodingBrain(
        model=Model(),
        inspector=GitRepositoryInspector(lambda repo, path: 'print("old")\n'),
        validator=Runner(),
    )
    result = brain.run(make_proposal())
    assert result.status.value == "ready_for_approval"
    assert result.approval_required is True


def test_inspector_redacts_secrets_and_bounds_context():
    inspector = GitRepositoryInspector(
        lambda repo, path: "API_KEY=supersecret\n" + ("x" * 100_000)
    )
    context = inspector.inspect("owner/repo", ["app.py"])
    assert "supersecret" not in context.files[0].content
    assert len(context.files[0].content) == 40_000
    assert context.truncated is True


def test_inspector_materializes_paths_from_one_shot_iterable():
    seen = []

    def reader(repo, path):
        seen.append(path)
        return path

    paths = (path for path in ("a.py", "b.py"))
    context = GitRepositoryInspector(reader).inspect("owner/repo", paths)

    assert seen == ["a.py", "b.py"]
    assert [item.path for item in context.files] == ["a.py", "b.py"]
    assert context.truncated is False


def test_inspector_marks_file_limit_without_consuming_paths_twice():
    seen = []

    def reader(repo, path):
        seen.append(path)
        return path

    paths = (f"{index}.py" for index in range(31))
    context = GitRepositoryInspector(reader).inspect("owner/repo", paths)

    assert len(seen) == 30
    assert len(context.files) == 30
    assert context.truncated is True


def test_prompt_contains_constraints_and_context():
    context = RepositoryContext("owner/repo", (RepositoryFile("app.py", "print(1)"),))
    prompt = build_model_prompt(make_proposal(), context, feedback="tests failed")
    assert "reviewable patch candidate" in prompt
    assert "tests failed" in prompt
    assert "app.py" in prompt
