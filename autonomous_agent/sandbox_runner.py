from __future__ import annotations
import os, shlex, shutil, signal, subprocess, tempfile
from pathlib import Path
from .self_improvement import PatchCandidate, ValidationResult

_ALLOWED=("python -m pytest","python -m unittest","python -m compileall")

class LocalSandboxTestRunner:
    """Run bounded validation inside a temporary workspace with network isolation."""
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

    @staticmethod
    def _network_prefix():
        unshare = shutil.which("unshare")
        if not unshare or os.name != "posix":
            return None
        return (unshare, "--user", "--map-root-user", "--net", "--mount-proc", "--")

    @staticmethod
    def _safe_env(root):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "HOME": str(root),
            "USERPROFILE": str(root),
        }
        if "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        if "WINDIR" in os.environ:
            env["WINDIR"] = os.environ["WINDIR"]
        return env

    @staticmethod
    def _kill(process):
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
        prefix = self._network_prefix()
        if prefix is None:
            return ValidationResult(False, "network-isolated validation is unavailable; sandbox refused subprocess execution")
        with tempfile.TemporaryDirectory(prefix="autonomous-scout-test-") as tmp:
            root=Path(tmp)
            shutil.copytree(self.workspace, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".env", ".env.*", "state", "__pycache__"))
            for path,content in candidate.file_contents.items():
                target=(root/path).resolve()
                if root not in target.parents: return ValidationResult(False,"candidate path escapes sandbox")
                target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content,encoding="utf-8")
            env = self._safe_env(root)
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            for command in commands:
                try:
                    argv = tuple(shlex.split(command, posix=os.name != "nt"))
                    process = subprocess.Popen(prefix + argv,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,shell=False,env=env,start_new_session=(os.name == "posix"))
                    output,_ = process.communicate(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    output,_ = process.communicate()
                    return ValidationResult(False,f"validation timed out: {command[:120]}")
                except OSError as exc:
                    return ValidationResult(False,f"validation could not start: {type(exc).__name__}")
                if process.returncode!=0:
                    detail=(output or "").strip()
                    return ValidationResult(False,f"{command[:120]} failed: {detail[-2000:]}")
        return ValidationResult(True,"sandbox validation passed")
