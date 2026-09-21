from pathlib import Path
from autonomous_agent.sandbox_runner import LocalSandboxTestRunner
from autonomous_agent.self_improvement import PatchCandidate

def test_sandbox_runner_executes_allowed_test(tmp_path:Path):
    (tmp_path/"test_ok.py").write_text("""def test_ok():\n    assert 1+1==2\n""",encoding="utf-8")
    proposal=type("P",(),{"validation_strategy":("python -m pytest -q",)})()
    result=LocalSandboxTestRunner(tmp_path).validate(proposal,PatchCandidate("",{},"tests",("python -m pytest -q",)))
    assert result.passed

def test_sandbox_runner_rejects_model_commands_outside_proposal_allowlist(tmp_path:Path):
    proposal=type("P",(),{"validation_strategy":("python -m pytest -q",)})()
    result=LocalSandboxTestRunner(tmp_path).validate(proposal,PatchCandidate("","", "bad", ("pytest --help",)))
    assert not result.passed

def test_sandbox_runner_rejects_shell_commands(tmp_path:Path):
    result=LocalSandboxTestRunner(tmp_path).validate(None,PatchCandidate("",{},"bad",("python -c print(1)",)))
    assert not result.passed
def test_sandbox_runner_does_not_inherit_host_environment_secrets(tmp_path: Path, monkeypatch):
    (tmp_path/"test_env.py").write_text(
        """import os

def test_secret_not_visible():
    assert os.getenv("SCOUT_TEST_SECRET") is None
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCOUT_TEST_SECRET", "must-not-leak")
    result = LocalSandboxTestRunner(tmp_path).validate(
        None,
        PatchCandidate("", {}, "env isolation", ("python -m pytest -q test_env.py",)),
    )
    assert result.passed

def test_sandbox_runner_rejects_absolute_and_parent_paths_in_commands(tmp_path: Path):
    for command in (
        "python -m pytest ../outside.py",
        "python -m compileall /tmp/outside",
        "pytest C:/outside",
    ):
        result = LocalSandboxTestRunner(tmp_path).validate(
            None,
            PatchCandidate("", {}, "bad", (command,)),
        )
        assert not result.passed
