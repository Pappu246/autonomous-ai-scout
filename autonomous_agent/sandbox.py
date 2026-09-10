from __future__ import annotations

import ast
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 64 * 1024
MAX_READ_BYTES = 128 * 1024
MAX_FILES = 5000
SAFE_OPERATIONS = {"inspect", "test", "lint", "metrics", "read_file", "benchmark"}


@dataclass(frozen=True)
class SandboxResult:
    operation: str
    success: bool
    exit_status: int | None
    output: str
    output_truncated: bool
    command: tuple[str, ...]
    verification_status: str
    started_at: str
    finished_at: str
    network_disabled: bool


@dataclass(frozen=True)
class ExecutionRecord:
    action_id: str
    approval_id: str
    timestamp: str
    category: str
    command: str
    result: str
    exit_status: int | None
    output_summary: str
    verification_status: str


def _text_limit(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated


def _root(root: Path) -> Path:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError("sandbox root is not a valid project directory")
    return resolved


def _inside(root: Path, target: Path) -> Path:
    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("sandbox target escapes the project root") from exc
    return resolved


def _safe_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }


def _network_prefix() -> tuple[str, ...] | None:
    unshare = shutil.which("unshare")
    if not unshare or os.name != "posix":
        return None
    return (unshare, "--user", "--map-root-user", "--net", "--mount-proc", "--")


def _kill(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass


def _run_test(root: Path, timeout_seconds: int, output_limit: int) -> tuple[int | None, str, bool, tuple[str, ...]]:
    prefix = _network_prefix()
    if prefix is None:
        return None, "network isolation unavailable; sandbox refused subprocess execution", False, ()
    command = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")
    final_command = prefix + command
    try:
        process = subprocess.Popen(
            final_command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_safe_env(),
            shell=False,
            start_new_session=True,
        )
        stdout, _ = process.communicate(timeout=max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS)))
    except subprocess.TimeoutExpired:
        _kill(process)
        stdout, _ = process.communicate()
        text, truncated = _text_limit((stdout or b"").decode("utf-8", errors="replace"), output_limit)
        return process.returncode, "timeout\n" + text, truncated, command
    except OSError as exc:
        return None, f"sandbox subprocess could not start: {exc}", False, command
    text, truncated = _text_limit((stdout or b"").decode("utf-8", errors="replace"), output_limit)
    return process.returncode, text, truncated, command


def run_safe_operation(
    operation: str,
    root: Path,
    target: str | None = None,
    *,
    timeout_seconds: int = 30,
    output_limit: int = MAX_OUTPUT_BYTES,
) -> SandboxResult:
    """Run exactly one allow-listed local operation; arbitrary commands are impossible by API design."""
    started = datetime.now(timezone.utc).isoformat()
    op = operation.strip().lower()
    limit = max(1, min(int(output_limit), MAX_OUTPUT_BYTES))
    root_path = _root(root)

    if op not in SAFE_OPERATIONS:
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, False, None, "operation is outside the sandbox allowlist", False, (), "blocked", started, finished, True)

    if op == "inspect":
        files: list[str] = []
        for path in sorted(root_path.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue
            files.append(str(path.relative_to(root_path)))
            if len(files) >= MAX_FILES:
                break
        output, truncated = _text_limit("Workspace files:\n" + "\n".join(f"- {item}" for item in files), limit)
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, True, 0, output, truncated, (), "verified", started, finished, True)

    if op == "metrics":
        file_count = 0
        total_bytes = 0
        for path in root_path.rglob("*"):
            if ".git" in path.parts or not path.is_file():
                continue
            file_count += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            if file_count >= MAX_FILES:
                break
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, True, 0, f"files={file_count}\ntotal_bytes={total_bytes}", False, (), "verified", started, finished, True)

    if op == "read_file":
        if not target:
            finished = datetime.now(timezone.utc).isoformat()
            return SandboxResult(op, False, None, "read_file requires a target", False, (), "blocked", started, finished, True)
        try:
            target_path = _inside(root_path, root_path / target)
            if not target_path.is_file():
                raise ValueError("sandbox target is not a file")
            if target_path.stat().st_size > MAX_READ_BYTES:
                raise ValueError("sandbox read target exceeds the size limit")
            output = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            finished = datetime.now(timezone.utc).isoformat()
            return SandboxResult(op, False, None, str(exc), False, (), "failed", started, finished, True)
        output, truncated = _text_limit(output, limit)
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, True, 0, output, truncated, (), "verified", started, finished, True)

    if op == "lint":
        failures: list[str] = []
        checked = 0
        for path in sorted(root_path.rglob("*.py")):
            if ".git" in path.parts:
                continue
            checked += 1
            if checked > MAX_FILES:
                break
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                failures.append(f"{path.relative_to(root_path)}: {exc}")
                if len(failures) >= 50:
                    break
        output, truncated = _text_limit(f"checked={checked}\n" + ("\n".join(failures) if failures else "syntax checks passed"), limit)
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, not failures, 0 if not failures else 1, output, truncated, (), "verified" if not failures else "failed", started, finished, True)

    if op == "benchmark":
        samples = 10000
        checksum = sum(range(samples))
        finished = datetime.now(timezone.utc).isoformat()
        return SandboxResult(op, True, 0, f"deterministic_local_benchmark={checksum}", False, (), "verified", started, finished, True)

    exit_status, output, truncated, command = _run_test(root_path, timeout_seconds, limit)
    success = exit_status == 0
    finished = datetime.now(timezone.utc).isoformat()
    return SandboxResult(op, success, exit_status, output, truncated, command, "verified" if success else "failed", started, finished, True)


def to_execution_record(action_id: str, approval_id: str, result: SandboxResult) -> ExecutionRecord:
    return ExecutionRecord(
        action_id=action_id,
        approval_id=approval_id,
        timestamp=result.finished_at,
        category=result.operation,
        command=" ".join(result.command),
        result="success" if result.success else "failure",
        exit_status=result.exit_status,
        output_summary=result.output[:2000],
        verification_status=result.verification_status,
    )
