from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_runs_pytest_on_push_and_pull_request() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "push:" in text
    assert "pytest -q" in text


def test_ci_workflow_does_not_enable_deployment_or_merge() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "deploy" not in text
    assert "merge" not in text
