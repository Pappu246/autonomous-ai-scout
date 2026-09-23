from pathlib import Path

from autonomous_agent.task_intent import TaskIntent, classify_intent
from autonomous_agent.tool_router import DynamicToolRouter
from autonomous_agent.workspace_shell import ControlledWorkspaceShell


def test_workspace_shell_supports_root_bound_read_commands(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    shell = ControlledWorkspaceShell(tmp_path)
    assert shell.run(("pwd",)).success
    listed = shell.run(("ls",)).output.splitlines()
    assert "hello.txt" in listed
    read = shell.run(("cat", "hello.txt"))
    assert read.success and read.output == "hello"


def test_workspace_shell_blocks_escape_and_metacharacters(tmp_path: Path):
    shell = ControlledWorkspaceShell(tmp_path)
    escaped = shell.run(("cat", "../secret.txt"))
    injected = shell.run(("ls", ";rm", "-rf"))
    unknown = shell.run(("curl", "https://example.com"))
    assert not escaped.success
    assert not injected.success
    assert not unknown.success


def test_workspace_shell_only_allows_isolated_py_compile(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    shell = ControlledWorkspaceShell(tmp_path)
    result = shell.run(("python", "-m", "py_compile", "sample.py"))
    if not result.success:
        assert "unshare" in result.output or "network-isolated shell execution is unavailable" in result.reason
    else:
        assert result.exit_status == 0


def test_shell_request_is_routed_as_workspace_shell():
    assert classify_intent("run a shell command") is TaskIntent.WORKSPACE
    assert DynamicToolRouter().select("run a shell command") == ("workspace.shell",)
def test_shell_rejects_sensitive_targets(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    result = ControlledWorkspaceShell(tmp_path).run(("cat", ".env"))
    assert not result.success


def test_shell_redacts_secret_like_read_output(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("safe\nAPI_KEY=supersecret\n", encoding="utf-8")
    result = ControlledWorkspaceShell(tmp_path).run(("cat", "notes.txt"))
    assert result.success
    assert "supersecret" not in result.output
    assert "[REDACTED]" in result.output
