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


def test_hourly_workflow_uses_immutable_actions_and_least_privilege_publish() -> None:
    workflow = _workflow("hourly-scout.yml")

    assert "permissions:\n  contents: read" in workflow
    assert "scout:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    permissions:\n      contents: read" in workflow
    assert "publish:\n    needs: scout" in workflow
    assert "permissions:\n      contents: write" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1" in workflow
    assert "persist-credentials: false" in workflow
    assert 'expected="${{ github.sha }}"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$expected"' in workflow
    assert 'test "\\$(git rev-parse HEAD)" = "\\$expected"' not in workflow
    assert "ref: autonomous-scout-state" in workflow
    assert "refs/heads/autonomous-scout-state" in workflow
    assert 'git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"' in workflow
    assert "http.extraheader=AUTHORIZATION: bearer ${GITHUB_TOKEN}" not in workflow
    assert "git push --force-with-lease" not in workflow
    assert "git fetch origin refs/heads/autonomous-ai-scout-state:refs/remotes/origin/autonomous-ai-scout-state" not in workflow


def test_hourly_artifact_download_uses_runner_temp_expression() -> None:
    workflow = _workflow("hourly-scout.yml")

    assert "path: ${{ runner.temp }}/scout-state" in workflow
    assert "path: $RUNNER_TEMP/scout-state" not in workflow
