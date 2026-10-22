from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES=128*1024; MAX_OUTPUT_BYTES=64*1024; MAX_ENTRIES=500; MAX_RETRIES=2; MAX_TIMEOUT_SECONDS=30
_SENSITIVE_FIELD_PATTERN = r"(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|token|authorization|password|secret|cookie|session|credential)"
_SECRET = re.compile(rf"(?i)(?<![\w-])([\"']?{_SENSITIVE_FIELD_PATTERN}[\"']?\s*[:=]\s*[\"']?)(?:(?:Bearer|Basic|Token)\s+)?([^\s,;\"'\}}\]]+)")
_CREDENTIAL_NAMES={".npmrc",".pypirc",".netrc",".git-credentials","credentials.json","service-account.json","id_rsa","id_ed25519"}
def _forbidden(parts):
    return bool(parts and parts[0] == "state") or any(
        part in {".git", ".env", ".ssh"} or part.startswith(".env.")
        or part in _CREDENTIAL_NAMES or part.endswith((".pem", ".key"))
        for part in parts
    )
class WorkspaceError(ValueError):pass
@dataclass(frozen=True)
class WorkspaceEvidence:
    operation:str; relative_path:str; fingerprint:str; content:str=""; entries:tuple[str,...]=(); redacted:bool=False
    def safe_dict(self):return {"operation":self.operation,"relative_path":self.relative_path,"fingerprint":self.fingerprint,"content":_redact(self.content),"entries":self.entries,"redacted":self.redacted}
def _redact(text):return _SECRET.sub(lambda m:m.group(1)+"[REDACTED]",text)
def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str).encode()).hexdigest()
class WorkspaceConnector:
    def __init__(self,root:Path,*,max_file_bytes=MAX_FILE_BYTES,max_output_bytes=MAX_OUTPUT_BYTES,max_entries=MAX_ENTRIES,max_retries=MAX_RETRIES,timeout_seconds=10):
        self.root=Path(root).resolve()
        if not self.root.is_dir():raise WorkspaceError("workspace root must be an existing directory")
        if not 1<=max_file_bytes<=MAX_FILE_BYTES or not 1<=max_output_bytes<=MAX_OUTPUT_BYTES or not 1<=max_entries<=MAX_ENTRIES or not 0<=max_retries<=MAX_RETRIES or not 1<=timeout_seconds<=MAX_TIMEOUT_SECONDS:raise WorkspaceError("unsafe workspace bounds")
        self.max_file_bytes=max_file_bytes;self.max_output_bytes=max_output_bytes;self.max_entries=max_entries;self.max_retries=max_retries;self.timeout_seconds=timeout_seconds
    def _path(self,relative):
        if not isinstance(relative,str) or not relative.strip():raise WorkspaceError("workspace path is required")
        candidate=(self.root/relative).resolve()
        try:candidate.relative_to(self.root)
        except ValueError as exc:raise WorkspaceError("workspace path escapes authorized root") from exc
        parts=candidate.relative_to(self.root).parts
        if _forbidden(parts):raise WorkspaceError("credential or private runtime paths are not authorized")
        return candidate
    def list(self,relative="."):
        path=self._path(relative)
        if not path.is_dir():raise WorkspaceError("workspace target is not a directory")
        entries=[]
        for item in sorted(path.iterdir(),key=lambda p:p.name):
            try:resolved=item.resolve();relative_parts=resolved.relative_to(self.root).parts
            except (OSError,ValueError):continue
            if _forbidden(relative_parts):continue
            entries.append(str(resolved.relative_to(self.root)))
            if len(entries)>=self.max_entries:break
        return WorkspaceEvidence("list",str(path.relative_to(self.root)),_digest(entries),entries=tuple(entries))
    def read(self,relative):
        path=self._path(relative)
        if not path.is_file():raise WorkspaceError("workspace target is not a file")
        if path.stat().st_size>=self.max_file_bytes:raise WorkspaceError("file exceeds workspace size limit")
        try:text=path.read_text(encoding="utf-8")
        except UnicodeError as exc:raise WorkspaceError("unsupported or invalid UTF-8 content") from exc
        safe=_redact(text);safe=safe[:self.max_output_bytes]
        return WorkspaceEvidence("read",str(path.relative_to(self.root)),_digest({"path":str(path.relative_to(self.root)),"content":text}),safe,redacted=safe!=text)
    def write(self,relative,content):
        path=self._path(relative)
        if path.exists() and path.is_dir():raise WorkspaceError("workspace target is a directory")
        if not isinstance(content,str) or len(content.encode())>=self.max_file_bytes:raise WorkspaceError("write content exceeds workspace size limit")
        if _SECRET.search(content):raise WorkspaceError("secret-like content is not permitted")
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding="utf-8",newline="")
        return WorkspaceEvidence("write",str(path.relative_to(self.root)),_digest({"path":str(path.relative_to(self.root)),"content":content}),content=content)
    def transform(self,relative,find,replace):
        evidence=self.read(relative)
        if evidence.redacted:raise WorkspaceError("transform refuses content requiring secret redaction")
        if not isinstance(find,str) or not find:raise WorkspaceError("transform find text is required")
        updated=evidence.content.replace(find,str(replace))
        if updated==evidence.content:return evidence
        return self.write(relative,updated)
