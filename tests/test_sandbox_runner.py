from pathlib import Path
from autonomous_agent.sandbox_runner import LocalSandboxTestRunner
from autonomous_agent.self_improvement import PatchCandidate

def test_sandbox_runner_executes_allowed_test(tmp_path:Path):
    (tmp_path/"test_ok.py").write_text("""def test_ok():\n    assert 1+1==2\n""",encoding="utf-8")
    result=LocalSandboxTestRunner(tmp_path).validate(None,PatchCandidate("",{},"tests",("python -m pytest -q",)))
    assert result.passed

def test_sandbox_runner_rejects_model_commands_outside_proposal_allowlist(tmp_path:Path):
    proposal=type("P",(),{"validation_strategy":("python -m pytest -q",)})()
    result=LocalSandboxTestRunner(tmp_path).validate(proposal,PatchCandidate("","", "bad", ("pytest --help",)))
    assert not result.passed

def test_sandbox_runner_rejects_shell_commands(tmp_path:Path):
    result=LocalSandboxTestRunner(tmp_path).validate(None,PatchCandidate("",{},"bad",("python -c print(1)",)))
    assert not result.passed

