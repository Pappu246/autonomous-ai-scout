"""Tests for application adapter models, exceptions, action budgets, and security policy."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.application import (
    ALLOWED_ADAPTER_COMMANDS,
    DANGEROUS_FLAGS,
    DENYLISTED_EXECUTABLES,
    FORBIDDEN_METACHARS,
    MAX_ACTION_BUDGET,
    MAX_APP_NAME_LENGTH,
    MAX_ARG_LENGTH,
    MAX_ARGV_COUNT,
    MAX_COMMAND_LENGTH,
    MAX_DOCUMENT_PATH_LENGTH,
    MAX_TEXT_PAYLOAD_LENGTH,
    REDACTED,
    ActionBudget,
    ActionBudgetExceededError,
    AdapterType,
    AdapterUnavailableError,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationError,
    ApplicationNotFoundError,
    ApplicationObservation,
    ApplicationReplayError,
    ApplicationSecurityError,
    ApplicationSessionError,
    ApplicationSessionSnapshot,
    ApplicationState,
    ApplicationTarget,
    assert_not_denylisted,
    assert_safe_flag,
    confine_document_path,
    confine_workspace_path,
    consequential_signal,
    is_denylisted_executable,
    looks_like_secret,
    redact_secret,
    validate_adapter_command,
    validate_app_launch,
    validate_application_name,
    validate_command_arguments,
    validate_text_payload,
)


# --------------------------------------------------------------------------
# Exceptions and Inheritance
# --------------------------------------------------------------------------
def test_exception_hierarchy():
    assert issubclass(ApplicationError, Exception)
    assert issubclass(ApplicationSecurityError, ApplicationError)
    assert issubclass(ApplicationNotFoundError, ApplicationError)
    assert issubclass(ApplicationSessionError, ApplicationError)
    assert issubclass(ApplicationReplayError, ApplicationError)
    assert issubclass(ActionBudgetExceededError, ApplicationError)
    assert issubclass(AdapterUnavailableError, ApplicationError)


# --------------------------------------------------------------------------
# ActionBudget
# --------------------------------------------------------------------------
def test_action_budget_lifecycle():
    budget = ActionBudget(limit=5)
    assert budget.limit == 5
    assert budget.used == 0
    assert budget.remaining == 5

    budget.consume(2)
    assert budget.used == 2
    assert budget.remaining == 3

    budget.consume(3)
    assert budget.used == 5
    assert budget.remaining == 0

    with pytest.raises(ActionBudgetExceededError, match="budget exceeded"):
        budget.consume(1)

    budget.reset()
    assert budget.used == 0
    assert budget.remaining == 5


def test_action_budget_invalid_parameters():
    with pytest.raises(ValueError, match="limit must be positive"):
        ActionBudget(limit=0)
    with pytest.raises(ValueError, match="limit must be positive"):
        ActionBudget(limit=-5)

    budget = ActionBudget(limit=10)
    with pytest.raises(ValueError, match="count must be positive"):
        budget.consume(0)
    with pytest.raises(ValueError, match="count must be positive"):
        budget.consume(-1)


# --------------------------------------------------------------------------
# Redaction and Secret Detection
# --------------------------------------------------------------------------
def test_looks_like_secret():
    assert looks_like_secret("api_key = abcdef123456789") is True
    assert looks_like_secret("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9") is True
    assert looks_like_secret("password: supersecretpassword") is True
    assert looks_like_secret("private_key: 0123456789abcdef") is True
    assert looks_like_secret("This is a normal document text.") is False
    assert looks_like_secret("") is False
    assert looks_like_secret(None) is False


def test_redact_secret():
    text = "Authorization: Bearer token123 and api_key=secret_val"
    redacted = redact_secret(text)
    assert "token123" not in redacted
    assert "secret_val" not in redacted
    assert REDACTED in redacted
    assert redact_secret(12345) == 12345


# --------------------------------------------------------------------------
# Consequential Action Signals
# --------------------------------------------------------------------------
def test_consequential_signal():
    assert consequential_signal("Click on checkout and complete payment") != ""
    assert consequential_signal("Permanently delete account") != ""
    assert consequential_signal("Change password for admin user") != ""
    assert consequential_signal("Export credentials to disk") != ""
    assert consequential_signal("Format drive C:") != ""
    assert consequential_signal("Drop database tables") != ""
    assert consequential_signal("Open document report.docx") == ""
    assert consequential_signal("Read current selection") == ""
    assert consequential_signal("", None) == ""


# --------------------------------------------------------------------------
# Data Models and Serialization
# --------------------------------------------------------------------------
def test_application_descriptor():
    desc = ApplicationDescriptor(
        app_id="vscode",
        name="Visual Studio Code",
        adapter_type=AdapterType.IDE,
        executable="code",
        supported_extensions=(".py", ".ts", ".md"),
        allowed_commands=("open_document", "save_document"),
        requires_approval=True,
        safe_autonomous=False,
        description="VS Code editor adapter",
    )
    data = desc.safe_dict()
    assert data["app_id"] == "vscode"
    assert data["name"] == "Visual Studio Code"
    assert data["adapter_type"] == "ide"
    assert data["requires_approval"] is True
    assert data["safe_autonomous"] is False
    assert data["supported_extensions"] == (".py", ".ts", ".md")


def test_application_target():
    target = ApplicationTarget(
        target_id="tgt-1",
        app_id="word",
        document_path="docs/spec.docx",
        view_name="main_editor",
        epoch=1,
    )
    data = target.safe_dict()
    assert data["target_id"] == "tgt-1"
    assert data["app_id"] == "word"
    assert data["document_path"] == "docs/spec.docx"
    assert data["view_name"] == "main_editor"
    assert data["epoch"] == 1


def test_application_command():
    target = ApplicationTarget("t1", "excel", "sheet.xlsx")
    cmd = ApplicationCommand(
        command="write_text",
        app_id="excel",
        args=("Cell A1",),
        payload="Quarterly revenue: $1000",
        target=target,
        timeout_seconds=15.0,
    )
    data = cmd.safe_dict()
    assert data["command"] == "write_text"
    assert data["app_id"] == "excel"
    assert data["args"] == ("Cell A1",)
    assert data["payload"] == "Quarterly revenue: $1000"
    assert data["target"]["target_id"] == "t1"
    assert data["timeout_seconds"] == 15.0


def test_application_observation():
    obs = ApplicationObservation(
        app_id="word",
        session_id="sess-01",
        state=ApplicationState.OPEN,
        active_document="docs/report.docx",
        status_message="Document opened successfully",
        data={"words": 450},
        epoch=2,
    )
    data = obs.safe_dict()
    assert data["app_id"] == "word"
    assert data["session_id"] == "sess-01"
    assert data["state"] == "open"
    assert data["active_document"] == "docs/report.docx"
    assert data["epoch"] == 2


def test_application_session_snapshot():
    snap = ApplicationSessionSnapshot(
        session_id="sess-01",
        app_id="excel",
        state=ApplicationState.OPEN,
        active_document="finances.xlsx",
        epoch=3,
        action_count=4,
        completed_actions=("open_document", "write_text"),
    )
    data = snap.safe_dict()
    assert data["session_id"] == "sess-01"
    assert data["app_id"] == "excel"
    assert data["state"] == "open"
    assert data["active_document"] == "finances.xlsx"
    assert data["epoch"] == 3
    assert data["action_count"] == 4
    assert data["completed_actions"] == ["open_document", "write_text"]


# --------------------------------------------------------------------------
# Denylists and Executable Security Policy
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "denylisted",
    [
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "bash",
        "/bin/bash",
        "/usr/bin/sh",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "regedit.exe",
        "rundll32.exe",
        "curl.exe",
        "wget",
        "taskkill.exe",
        "shutdown.exe",
        "python",
        "python3.exe",
        "node",
        "ruby",
    ],
)
def test_denylisted_executables_blocked(denylisted: str):
    assert is_denylisted_executable(denylisted) is True
    with pytest.raises(ApplicationSecurityError, match="denylisted"):
        assert_not_denylisted(denylisted)


def test_empty_or_metachar_executables_rejected():
    with pytest.raises(ApplicationSecurityError, match="cannot be empty"):
        assert_not_denylisted("")
    with pytest.raises(ApplicationSecurityError, match="cannot be empty"):
        assert_not_denylisted("   ")
    with pytest.raises(ApplicationSecurityError, match="forbidden metacharacter"):
        assert_not_denylisted("app; rm -rf /")
    with pytest.raises(ApplicationSecurityError, match="forbidden metacharacter"):
        assert_not_denylisted("app && echo pwned")


# --------------------------------------------------------------------------
# Application Name Validation
# --------------------------------------------------------------------------
def test_validate_application_name():
    assert validate_application_name("Visual Studio Code") == "Visual Studio Code"
    assert validate_application_name("Microsoft Excel") == "Microsoft Excel"

    with pytest.raises(ApplicationSecurityError, match="must be a string"):
        validate_application_name(123)
    with pytest.raises(ApplicationSecurityError, match="cannot be empty"):
        validate_application_name("")
    with pytest.raises(ApplicationSecurityError, match="cannot be empty"):
        validate_application_name("   ")
    with pytest.raises(ApplicationSecurityError, match="exceeds max length"):
        validate_application_name("a" * (MAX_APP_NAME_LENGTH + 1))
    with pytest.raises(ApplicationSecurityError, match="forbidden metacharacter"):
        validate_application_name("code | calc")


# --------------------------------------------------------------------------
# Adapter Command Validation
# --------------------------------------------------------------------------
def test_validate_adapter_command():
    assert validate_adapter_command("open_document") == "open_document"
    assert validate_adapter_command("SAVE_DOCUMENT") == "save_document"
    assert validate_adapter_command("read_selection") == "read_selection"

    with pytest.raises(ApplicationSecurityError, match="must be a string"):
        validate_adapter_command(None)
    with pytest.raises(ApplicationSecurityError, match="cannot be empty"):
        validate_adapter_command("")
    with pytest.raises(ApplicationSecurityError, match="unsupported or unallowed"):
        validate_adapter_command("execute_arbitrary_script")
    with pytest.raises(ApplicationSecurityError, match="forbidden metacharacter"):
        validate_adapter_command("open_document; evil")
    with pytest.raises(ApplicationSecurityError, match="exceeds max length"):
        validate_adapter_command("a" * (MAX_COMMAND_LENGTH + 1))


# --------------------------------------------------------------------------
# Dangerous Flags and Arguments Validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "flag",
    [
        "--inspect",
        "--inspect-brk",
        "--remote-debugging-port=9222",
        "--remote-debugging-pipe",
        "--eval",
        "-e",
        "--exec",
        "-c",
        "--command",
        "--no-sandbox",
        "--disable-web-security",
        "--allow-file-access-from-files",
        "--load-extension",
    ],
)
def test_dangerous_flags_blocked(flag: str):
    with pytest.raises(ApplicationSecurityError, match="dangerous or security-weakening"):
        assert_safe_flag(flag)
    with pytest.raises(ApplicationSecurityError, match="dangerous or security-weakening"):
        validate_command_arguments([flag])


def test_validate_command_arguments():
    assert validate_command_arguments(["--readonly", "document.docx"]) == ("--readonly", "document.docx")
    assert validate_command_arguments([]) == ()

    with pytest.raises(ApplicationSecurityError, match="must be a list or tuple"):
        validate_command_arguments("not a list")
    with pytest.raises(ApplicationSecurityError, match="items must be strings"):
        validate_command_arguments([123])
    with pytest.raises(ApplicationSecurityError, match="exceeds max allowed"):
        validate_command_arguments(["arg"] * (MAX_ARGV_COUNT + 1))
    with pytest.raises(ApplicationSecurityError, match="exceeds max length"):
        validate_command_arguments(["a" * (MAX_ARG_LENGTH + 1)])
    with pytest.raises(ApplicationSecurityError, match="forbidden metacharacter"):
        validate_command_arguments(["valid", "arg; echo bad"])
    with pytest.raises(ApplicationSecurityError, match="credentials must not leak"):
        validate_command_arguments(["api_key=supersecrettoken123"])


# --------------------------------------------------------------------------
# Text Payload Validation
# --------------------------------------------------------------------------
def test_validate_text_payload():
    assert validate_text_payload("Valid text content") == "Valid text content"

    with pytest.raises(ApplicationSecurityError, match="must be a string"):
        validate_text_payload(12345)
    with pytest.raises(ApplicationSecurityError, match="exceeds max allowed length"):
        validate_text_payload("a" * (MAX_TEXT_PAYLOAD_LENGTH + 1))
    with pytest.raises(ApplicationSecurityError, match="credentials must not be written"):
        validate_text_payload("My secret password: topsecretpass123")


# --------------------------------------------------------------------------
# Workspace and Document Path Confinement
# --------------------------------------------------------------------------
def test_confine_workspace_path(tmp_path: Path):
    dest, norm = confine_workspace_path(tmp_path, "docs/subfolder/file.docx")
    assert dest == (tmp_path / "docs/subfolder/file.docx").resolve()
    assert norm == "docs/subfolder/file.docx"

    with pytest.raises(ApplicationSecurityError, match="workspace-relative path is required"):
        confine_workspace_path(tmp_path, "")
    with pytest.raises(ApplicationSecurityError, match="path traversal is not permitted"):
        confine_workspace_path(tmp_path, "../outside.docx")
    with pytest.raises(ApplicationSecurityError, match="path traversal is not permitted"):
        confine_workspace_path(tmp_path, "docs/../../outside.docx")
    with pytest.raises(ApplicationSecurityError, match="path traversal is not permitted"):
        confine_workspace_path(tmp_path, "/etc/passwd")


def test_confine_document_path(tmp_path: Path):
    dest, norm = confine_document_path(tmp_path, "report.docx", allowed_extensions=[".docx", ".pdf"])
    assert norm == "report.docx"

    with pytest.raises(ApplicationSecurityError, match="unsupported document extension"):
        confine_document_path(tmp_path, "script.py", allowed_extensions=[".docx", ".pdf"])


# --------------------------------------------------------------------------
# Application Launch Validation
# --------------------------------------------------------------------------
def test_validate_app_launch():
    name, args = validate_app_launch("libreoffice", ["--writer", "doc.odt"])
    assert name == "libreoffice"
    assert args == ("--writer", "doc.odt")

    with pytest.raises(ApplicationSecurityError, match="denylisted"):
        validate_app_launch("cmd.exe", ["/c", "dir"])

    allowed = frozenset({"libreoffice", "inkscape"})
    with pytest.raises(ApplicationSecurityError, match="not in the allowed list"):
        validate_app_launch("gimp", allowed_apps=allowed)
