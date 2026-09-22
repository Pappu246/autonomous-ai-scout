from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_COMMAND_ARGS = 8
MAX_OUTPUT_BYTES = 32 * 1024
MAX_TIMEOUT_SECONDS = 30

_SAFE_HEADS = {"pwd", "ls", "dir", "cat", "type", "python"}


@dataclass(frozen=True)
class WorkspaceShellResult:
    success: bool
    argv: tuple[str, ...]
    output: str
    exit_status: int | None
    reason: str


class ControlledWorkspaceShell:
    """Root-bound, argv-only read/test shell with no arbitrary shell expansion."""

    def __init__(self, root: str | Path) -> None:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise ValueError("workspace root is not a directory")
        self.root = resolved

    def run(self, argv: Iterable[str], *, timeout_seconds: int = 20) -> WorkspaceShellResult:
        parts = tuple(str(item) for item in argv)
        if not parts or len(parts) > MAX_COMMAND_ARGS:
            return WorkspaceShellResult(False, parts, "", None, "shell command must contain 1-8 argv entries")
        head = parts[0].lower()
        if head not in _SAFE_HEADS:
            return WorkspaceShellResult(False, parts, "", None, "command is outside the workspace shell allowlist")
        if any(any(token in value for token in ("&", "|", ";", "`", "$(", ">", "<")) for value in parts):
            return WorkspaceShellResult(False, parts, "", None, "shell metacharacters are not permitted")
        timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))

        if head == "pwd":
            return WorkspaceShellResult(True, parts, str(self.root), 0, "verified")
        if head in {"ls", "dir"}:
            target = self._safe_target(parts[1] if len(parts) > 1 else ".")
            if target is None:
                return WorkspaceShellResult(False, parts, "", None, "workspace path escapes the root")
            try:
                names = sorted(path.name for path in target.iterdir())[:500]
            except OSError as exc:
                return WorkspaceShellResult(False, parts, "", None, f"directory listing failed: {type(exc).__name__}")
            return WorkspaceShellResult(True, parts, "\n".join(names), 0, "verified")
        if head in {"cat", "type"}:
            if len(parts) != 2:
                return WorkspaceShellResult(False, parts, "", None, "cat/type requires one workspace-relative file")
            target = self._safe_target(parts[1])
            if target is None or not target.is_file():
                return WorkspaceShellResult(False, parts, "", None, "workspace file is invalid or missing")
            try:
                raw = target.read_bytes()
            except OSError as exc:
                return WorkspaceShellResult(False, parts, "", None, f"file read failed: {type(exc).__name__}")
            if len(raw) > MAX_OUTPUT_BYTES:
                raw = raw[:MAX_OUTPUT_BYTES]
            return WorkspaceShellResult(True, parts, raw.decode("utf-8", errors="replace"), 0, "verified")
        if head == "python":
            if tuple(parts[1:3]) != ("-m", "py_compile") or len(parts) != 4:
                return WorkspaceShellResult(False, parts, "", None, "python shell mode permits only python -m py_compile <file>")
            target = self._safe_target(parts[3])
            if target is None or target.suffix != ".py" or not target.is_file():
                return WorkspaceShellResult(False, parts, "", None, "py_compile target must be a workspace Python file")
            prefix = shutil.which("unshare")
            if prefix is None or os.name != "posix":
                return WorkspaceShellResult(False, parts, "", None, "network-isolated shell execution is unavailable")
            command = (prefix, "--user", "--map-root-user", "--net", "--mount-proc", "--", sys.executable, "-m", "py_compile", str(target.relative_to(self.root)))
            try:
                process = subprocess.run(command, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}, check=False, text=True, shell=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return WorkspaceShellResult(False, parts, "", None, f"py_compile failed to run: {type(exc).__name__}")
            return WorkspaceShellResult(process.returncode == 0, parts, process.stdout[:MAX_OUTPUT_BYTES], process.returncode, "verified" if process.returncode == 0 else "failed")
        return WorkspaceShellResult(False, parts, "", None, "unsupported workspace shell operation")

    def _safe_target(self, value: str) -> Path | None:
        candidate = (self.root / value).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate


__all__ = ["ControlledWorkspaceShell", "WorkspaceShellResult"]
