from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_ci_workflow_uses_immutable_action_commits() -> None:
    workflow = _workflow("ci.yml")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0" in workflow
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow


def test_hourly_workflow_uses_immutable_actions_and_safe_state_publication() -> None:
    workflow = _workflow("hourly-scout.yml")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0" in workflow
    assert "persist-credentials: false" in workflow
    assert 'expected="${{ github.sha }}"' in workflow
    assert 'git fetch origin refs/heads/autonomous-scout-state:refs/remotes/origin/autonomous-scout-state' in workflow
    assert "git switch --detach origin/autonomous-scout-state" in workflow
    assert "http.extraheader=AUTHORIZATION: bearer ${GITHUB_TOKEN}" in workflow
    assert "git push --force-with-lease" not in workflow
