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
