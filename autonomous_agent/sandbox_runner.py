from __future__ import annotations
import os, shlex, shutil, subprocess, tempfile
from pathlib import Path
from .self_improvement import PatchCandidate, ValidationResult

_ALLOWED=("python -m pytest","python -m unittest","python -m compileall")

class LocalSandboxTestRunner:
    """Run bounded non-shell test commands against a temporary workspace."""
    def __init__(self, workspace: str|Path, *, timeout_seconds:int=120):
        self.workspace=Path(workspace).resolve(); self.timeout_seconds=max(1,min(int(timeout_seconds),300))
    def _normalize_command(self, command:str)->str:
        normalized=" ".join(command.strip().split())
        if normalized == "python3":
            return "python"
        if normalized.startswith("python3 "):
            return "python " + normalized[len("python3 "):]
        return normalized

    def _allowed(self, command:str)->bool:
        normalized=self._normalize_command(command)
        if any(x in normalized for x in ("&&","||",";","|",">","<","$(","`")): return False
        if " -c " in f" {normalized} " or " --command " in f" {normalized} ": return False
        if not any(normalized==p or normalized.startswith(p+" ") for p in _ALLOWED): return False
        try:
            tokens = shlex.split(normalized, posix=os.name != "nt")
        except ValueError:
            return False
        if not tokens:
            return False
        for token in tokens[1:]:
            path_like = token.split("=", 1)[-1].replace("\\", "/")
            drive_like = len(path_like) >= 2 and path_like[1] == ":"
            traversal = (
                path_like.startswith("/")
                or path_like == ".."
                or path_like.startswith("../")
                or "/../" in path_like
                or path_like.endswith("/..")
            )
            if drive_like or traversal:
                return False
        return True
    def validate(self, proposal, candidate:PatchCandidate, *, context=None)->ValidationResult:
        candidate_commands = tuple(self._normalize_command(command) for command in candidate.test_commands)
        approved = tuple(getattr(proposal, "validation_strategy", ()) or ()) if proposal is not None else ()
        approved_commands = tuple(self._normalize_command(command) for command in approved if self._allowed(command))
        if candidate_commands:
            commands = candidate_commands
            if approved_commands:
                normalized_approved = set(approved_commands)
                normalized_candidate = set(candidate_commands)
                if not normalized_candidate.issubset(normalized_approved):
                    return ValidationResult(False, "model-supplied test command is not in the proposal validation allowlist")
        else:
            commands = approved_commands
        if not commands:
            return ValidationResult(False, "no executable validation command supplied")
        for command in commands:
            if not self._allowed(command):
                return ValidationResult(False,f"test command is not allowlisted: {command[:120]}")
        with tempfile.TemporaryDirectory(prefix="autonomous-scout-test-") as tmp:
            root=Path(tmp)
            shutil.copytree(self.workspace, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".env", ".env.*", "state", "__pycache__"))
            for path,content in candidate.file_contents.items():
                target=(root/path).resolve()
                if root not in target.parents: return ValidationResult(False,"candidate path escapes sandbox")
                target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content,encoding="utf-8")
            for command in commands:
                try:
                    env = {
                        key: os.environ[key]
                        for key in (
                            "PATH",
                            "PATHEXT",
                            "SYSTEMROOT",
                            "WINDIR",
                            "TEMP",
                            "TMP",
                            "TMPDIR",
                            "HOME",
                            "USERPROFILE",
                            "VIRTUAL_ENV",
                        )
                        if key in os.environ
                    }
                    env.update({
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    })
                    completed=subprocess.run(command.split(),cwd=root,capture_output=True,text=True,timeout=self.timeout_seconds,shell=False,env=env)
                except subprocess.TimeoutExpired: return ValidationResult(False,f"validation timed out: {command[:120]}")
                except OSError as exc: return ValidationResult(False,f"validation could not start: {type(exc).__name__}")
                if completed.returncode!=0:
                    detail=(completed.stdout+"\n"+completed.stderr).strip()
                    return ValidationResult(False,f"{command[:120]} failed: {detail[-2000:]}")
        return ValidationResult(True,"sandbox validation passed")
