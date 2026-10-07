from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "ai_finish_release.py"
    spec = importlib.util.spec_from_file_location("ai_finish_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ai_finish_release_module_imports_and_declares_gates():
    module = _load_module()
    assert module.GATE3 == "gate3-coding-provider-smoke.yml"
    assert module.GATE4 == "gate4-external-connector-smoke.yml"
    assert module.GATE5 == "gate5-github-admin-readiness.yml"


def test_workflow_run_model_is_immutable_and_structured():
    module = _load_module()
    run = module.WorkflowRun(
        run_id=123,
        head_sha="abc",
        status="completed",
        conclusion="success",
        event="workflow_dispatch",
    )
    assert run.run_id == 123
    assert run.head_sha == "abc"
    assert run.conclusion == "success"


def test_github_token_prefers_admin_token_without_exposing_value(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    monkeypatch.setenv("SCOUT_GITHUB_ADMIN_TOKEN", "admin-secret")
    assert module.github_token() == "admin-secret"


def test_dispatch_uses_rest_api_when_admin_token_is_present(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SCOUT_GITHUB_ADMIN_TOKEN", "admin-secret")
    calls = []

    def fake_api(method, path, *, repository, body=None):
        calls.append((method, path, repository, body))
        return None

    monkeypatch.setattr(module, "github_api", fake_api)
    module.dispatch(
        module.GATE5,
        "feat/mission-control-ui",
        "Pappu246/autonomous-ai-scout",
        apply_gate5=True,
    )

    assert len(calls) == 1
    method, path, repository, body = calls[0]
    assert method == "POST"
    assert path.endswith("/actions/workflows/gate5-github-admin-readiness.yml/dispatches")
    assert repository == "Pappu246/autonomous-ai-scout"
    assert body == {
        "ref": "feat/mission-control-ui",
        "inputs": {"apply_protection": "true"},
    }


def test_list_runs_uses_rest_api_response_shape(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SCOUT_GITHUB_ADMIN_TOKEN", "admin-secret")

    def fake_api(method, path, *, repository, body=None):
        assert method == "GET"
        assert "branch=feat%2Fmission-control-ui" in path
        assert "per_page=30" in path
        assert "event=" not in path
        return {
            "workflow_runs": [
                {
                    "id": 456,
                    "head_sha": "abc",
                    "status": "completed",
                    "conclusion": "success",
                    "event": "workflow_dispatch",
                }
            ]
        }

    monkeypatch.setattr(module, "github_api", fake_api)
    runs = module.list_runs(
        module.GATE5,
        "feat/mission-control-ui",
        "Pappu246/autonomous-ai-scout",
    )

    assert runs == [
        module.WorkflowRun(
            run_id=456,
            head_sha="abc",
            status="completed",
            conclusion="success",
            event="workflow_dispatch",
        )
    ]
