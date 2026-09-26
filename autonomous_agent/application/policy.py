"""Platform-independent security policy, allowlists/denylists, workspace confinement, and bounds for application adapters.

Every function fails closed: on any doubt or invalid parameter it raises
:class:`~autonomous_agent.application.models.ApplicationSecurityError` rather than
permitting the operation.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence

from .models import (
    MAX_ACTION_BUDGET,
    MAX_APP_NAME_LENGTH,
    MAX_ARG_LENGTH,
    MAX_ARGV_COUNT,
    MAX_COMMAND_LENGTH,
    MAX_DOCUMENT_PATH_LENGTH,
    MAX_TEXT_PAYLOAD_LENGTH,
    ApplicationSecurityError,
    consequential_signal,
    looks_like_secret,
    redact_secret,
)


DENYLISTED_EXECUTABLES: frozenset[str] = frozenset({
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "bash.exe",
    "sh",
    "sh.exe",
    "zsh",
    "zsh.exe",
    "wsl",
    "wsl.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    "mshta",
    "mshta.exe",
    "certutil",
    "certutil.exe",
    "reg",
    "reg.exe",
    "regedit",
    "regedit.exe",
    "rundll32",
    "rundll32.exe",
    "installutil",
    "installutil.exe",
    "bitsadmin",
    "bitsadmin.exe",
    "vssadmin",
    "vssadmin.exe",
    "format",
    "format.com",
    "diskpart",
    "diskpart.exe",
    "curl",
    "curl.exe",
    "wget",
    "wget.exe",
    "taskkill",
    "taskkill.exe",
    "shutdown",
    "shutdown.exe",
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "node",
    "node.exe",
    "perl",
    "ruby",
    "php",
})

FORBIDDEN_METACHARS: frozenset[str] = frozenset({
    ";", "&", "|", "`", "$", ">", "<", "\n", "\r", "\x00",
})

DANGEROUS_FLAGS: tuple[str, ...] = (
    "--inspect",
    "--inspect-brk",
    "--remote-debugging-port",
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
    "--disable-gpu-sandbox",
    "--enable-automation",
)

ALLOWED_ADAPTER_COMMANDS: frozenset[str] = frozenset({
    "open_document",
    "save_document",
    "close_document",
    "get_status",
    "read_selection",
    "write_text",
    "format_selection",
    "export_document",
    "get_metadata",
    "list_views",
    "focus_view",
    "undo",
    "redo",
})


def is_denylisted_executable(name: str) -> bool:
    """True when an executable name matches a blocked system binary or shell."""
    if not isinstance(name, str) or not name.strip():
        return True
    cleaned = name.strip()
    basename = PureWindowsPath(cleaned).name.lower()
    if not basename:
        basename = PurePosixPath(cleaned).name.lower()
    return basename in DENYLISTED_EXECUTABLES


def assert_not_denylisted(executable: str) -> None:
    """Fail closed if an application executable is denylisted."""
    if not isinstance(executable, str) or not executable.strip():
        raise ApplicationSecurityError("executable cannot be empty")
    cleaned = executable.strip()
    for ch in FORBIDDEN_METACHARS:
        if ch in cleaned:
            raise ApplicationSecurityError(
                f"forbidden metacharacter in executable name: {repr(ch)}"
            )
    if is_denylisted_executable(cleaned):
        raise ApplicationSecurityError(
            f"executable is denylisted for security: {cleaned}"
        )


def validate_application_name(name: str) -> str:
    """Validate application name string."""
    if not isinstance(name, str):
        raise ApplicationSecurityError("application name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise ApplicationSecurityError("application name cannot be empty")
    if len(cleaned) > MAX_APP_NAME_LENGTH:
        raise ApplicationSecurityError(
            f"application name exceeds max length: {len(cleaned)} > {MAX_APP_NAME_LENGTH}"
        )
    for ch in FORBIDDEN_METACHARS:
        if ch in cleaned:
            raise ApplicationSecurityError(
                f"forbidden metacharacter in application name: {repr(ch)}"
            )
    return cleaned


def validate_adapter_command(
    command: str,
    allowed_commands: frozenset[str] | None = None,
) -> str:
    """Validate an adapter command against length bounds, metacharacters, and allowlist."""
    if not isinstance(command, str):
        raise ApplicationSecurityError("adapter command must be a string")
    cleaned = command.strip().lower()
    if not cleaned:
        raise ApplicationSecurityError("adapter command cannot be empty")
    if len(cleaned) > MAX_COMMAND_LENGTH:
        raise ApplicationSecurityError(
            f"adapter command exceeds max length: {len(cleaned)} > {MAX_COMMAND_LENGTH}"
        )
    for ch in FORBIDDEN_METACHARS:
        if ch in cleaned:
            raise ApplicationSecurityError(
                f"forbidden metacharacter in adapter command: {repr(ch)}"
            )
    allowlist = allowed_commands if allowed_commands is not None else ALLOWED_ADAPTER_COMMANDS
    if cleaned not in allowlist:
        raise ApplicationSecurityError(
            f"unsupported or unallowed adapter command: {command}"
        )
    return cleaned


def assert_safe_flag(arg: str) -> None:
    """Fail closed if an argument contains dangerous debug/eval/script flags."""
    lower = arg.strip().lower()
    for flag in DANGEROUS_FLAGS:
        if lower == flag or lower.startswith(flag + "=") or lower.startswith(flag + ":"):
            raise ApplicationSecurityError(
                f"dangerous or security-weakening flag is forbidden: {arg}"
            )


def validate_command_arguments(args: Sequence[str]) -> tuple[str, ...]:
    """Validate argument list for adapter commands."""
    if not isinstance(args, (list, tuple)):
        raise ApplicationSecurityError("arguments must be a list or tuple of strings")
    if len(args) > MAX_ARGV_COUNT:
        raise ApplicationSecurityError(
            f"argument count exceeds max allowed: {len(args)} > {MAX_ARGV_COUNT}"
        )
    validated: list[str] = []
    for arg in args:
        if not isinstance(arg, str):
            raise ApplicationSecurityError("argument items must be strings")
        if len(arg) > MAX_ARG_LENGTH:
            raise ApplicationSecurityError(
                f"argument item exceeds max length {MAX_ARG_LENGTH}: {len(arg)}"
            )
        for ch in FORBIDDEN_METACHARS:
            if ch in arg:
                raise ApplicationSecurityError(
                    f"forbidden metacharacter in argument: {repr(ch)}"
                )
        assert_safe_flag(arg)
        if looks_like_secret(arg):
            raise ApplicationSecurityError(
                "credentials must not leak through command arguments"
            )
        validated.append(arg)
    return tuple(validated)


def validate_text_payload(text: str) -> str:
    """Validate text payload to insert or format in application."""
    if not isinstance(text, str):
        raise ApplicationSecurityError("payload must be a string")
    if len(text) > MAX_TEXT_PAYLOAD_LENGTH:
        raise ApplicationSecurityError(
            f"payload exceeds max allowed length: {len(text)} > {MAX_TEXT_PAYLOAD_LENGTH}"
        )
    if looks_like_secret(text):
        raise ApplicationSecurityError(
            "credentials must not be written to application documents"
        )
    return text


def confine_workspace_path(workspace_root: Path | str, relative_path: str) -> tuple[Path, str]:
    """Resolve a path strictly inside workspace root, rejecting path traversal."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ApplicationSecurityError("a workspace-relative path is required")
    root = Path(workspace_root).resolve()
    candidate = PurePosixPath(relative_path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ApplicationSecurityError("path traversal is not permitted")
    destination = (root / candidate).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ApplicationSecurityError("path escapes the workspace root") from exc
    return destination, str(candidate)


def confine_document_path(
    workspace_root: Path | str,
    relative_path: str,
    allowed_extensions: Sequence[str] | None = None,
) -> tuple[Path, str]:
    """Resolve and validate a document path strictly inside workspace root with optional extension filtering."""
    dest, norm = confine_workspace_path(workspace_root, relative_path)
    if allowed_extensions is not None:
        ext = dest.suffix.lower()
        norm_allowed = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in allowed_extensions}
        if ext not in norm_allowed:
            raise ApplicationSecurityError(
                f"unsupported document extension: {ext} (allowed: {sorted(norm_allowed)})"
            )
    return dest, norm


def validate_app_launch(
    app_name: str,
    args: Sequence[str] = (),
    allowed_apps: frozenset[str] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Validate application launch parameters against allowlists, denylists, bounds, and flags."""
    name = validate_application_name(app_name)
    assert_not_denylisted(name)
    if allowed_apps is not None and name.lower() not in allowed_apps:
        raise ApplicationSecurityError(f"application is not in the allowed list: {name}")
    validated_args = validate_command_arguments(args)
    return name, validated_args


__all__ = [
    "ALLOWED_ADAPTER_COMMANDS",
    "DANGEROUS_FLAGS",
    "DENYLISTED_EXECUTABLES",
    "FORBIDDEN_METACHARS",
    "assert_not_denylisted",
    "assert_safe_flag",
    "confine_document_path",
    "confine_workspace_path",
    "consequential_signal",
    "is_denylisted_executable",
    "looks_like_secret",
    "redact_secret",
    "validate_adapter_command",
    "validate_app_launch",
    "validate_application_name",
    "validate_command_arguments",
    "validate_text_payload",
]
