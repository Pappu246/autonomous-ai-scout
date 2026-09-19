from __future__ import annotations
import os, shutil, subprocess, tempfile
from pathlib import Path
from .self_improvement import PatchCandidate, ValidationResult

_ALLOWED=("python -m pytest","python -m unittest","python -m compileall","pytest")

class LocalSandboxTestRunner:
    """Run bounded non-shell test commands against a temporary workspace."""
    def __init__(self, workspace: str|Path, *, timeout_seconds:int=120):
        self.workspace=Path(workspace).resolve(); self.timeout_seconds=max(1,min(int(timeout_seconds),300))
    def _allowed(self, command:str)->bool:
        normalized=" ".join(command.strip().split())
        if any(x in normalized for x in ("&&","||",";","|",">","<","$(","`")): return False
        if " -c " in f" {normalized} " or " --command " in f" {normalized} ": return False
        return any(normalized==p or normalized.startswith(p+" ") for p in _ALLOWED)
    def validate(self, proposal, candidate:PatchCandidate, *, context=None)->ValidationResult:
        commands=candidate.test_commands or getattr(proposal,"validation_strategy",())
        if not commands: return ValidationResult(False,"no validation command supplied")
        for command in commands:
            if not self._allowed(command): return ValidationResult(False,f"test command is not allowlisted: {command[:120]}")
        with tempfile.TemporaryDirectory(prefix="autonomous-scout-test-") as tmp:
            root=Path(tmp)
            shutil.copytree(self.workspace, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".env", ".env.*", "state", "__pycache__"))
            approved = tuple(getattr(proposal, "validation_strategy", ()) or ()) if proposal is not None else ()
            if candidate.test_commands and approved and any(command not in approved for command in candidate.test_commands):
                return ValidationResult(False, "model-supplied test command is not in the proposal validation allowlist")
            for path,content in candidate.file_contents.items():
                target=(root/path).resolve()
                if root not in target.parents: return ValidationResult(False,"candidate path escapes sandbox")
                target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content,encoding="utf-8")
            for command in commands:
                try:
                    env = {"PATH": os.environ.get("PATH", "") , "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
                    completed=subprocess.run(command.split(),cwd=root,capture_output=True,text=True,timeout=self.timeout_seconds,shell=False,env=env)
                except subprocess.TimeoutExpired: return ValidationResult(False,f"validation timed out: {command[:120]}")
                except OSError as exc: return ValidationResult(False,f"validation could not start: {type(exc).__name__}")
                if completed.returncode!=0:
                    detail=(completed.stdout+"\n"+completed.stderr).strip()
                    return ValidationResult(False,f"{command[:120]} failed: {detail[-2000:]}")
        return ValidationResult(True,"sandbox validation passed")
