from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "windows-computer-smoke.yml"


def test_ci_workflow_runs_pytest_on_push_and_pull_request() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "push:" in text
    assert "pytest -q" in text


def test_ci_workflow_does_not_enable_deployment_or_merge() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "deploy" not in text
    assert "merge" not in text


def test_canonical_task_entrypoint_points_to_bounded_runtime() -> None:
    text = Path(__file__).parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert 'autonomous-scout-task = "autonomous_agent.runtime:main"' in text


def test_windows_computer_workflow_uses_immutable_action_pins() -> None:
    text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text
    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v5" not in text


def test_windows_computer_workflow_keeps_computer_smoke_scoped() -> None:
    text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    assert 'paths:' in text
    assert 'autonomous_agent/computer/**' in text
    assert 'tests/test_computer_windows_integration.py' in text
