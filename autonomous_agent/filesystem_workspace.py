from __future__ import annotations
import hashlib,json,re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
MAX_FILE_BYTES=128*1024; MAX_OUTPUT_BYTES=64*1024; MAX_ENTRIES=500; MAX_RETRIES=2; MAX_TIMEOUT_SECONDS=30
_SECRET=re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|cookie|session|credential)\s*[:=]\s*[^\s,;]+")
class WorkspaceError(ValueError):pass
@dataclass(frozen=True)
class WorkspaceEvidence:
    operation:str; relative_path:str; fingerprint:str; content:str=""; entries:tuple[str,...]=(); redacted:bool=False
    def safe_dict(self):return {"operation":self.operation,"relative_path":self.relative_path,"fingerprint":self.fingerprint,"content":_redact(self.content),"entries":self.entries,"redacted":self.redacted}
def _redact(text):return _SECRET.sub(lambda m:m.group(1)+"=[REDACTED]",text)
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
        if any(part in {".git",".env",".ssh"} for part in candidate.relative_to(self.root).parts):raise WorkspaceError("credential or VCS paths are not authorized")
        return candidate
    def list(self,relative="."):
        path=self._path(relative)
        if not path.is_dir():raise WorkspaceError("workspace target is not a directory")
        entries=[]
        for item in sorted(path.iterdir(),key=lambda p:p.name):
            if item.name in {".git",".env",".ssh"}:continue
            resolved=item.resolve()
            try:resolved.relative_to(self.root)
            except ValueError:continue
            entries.append(str(resolved.relative_to(self.root)))
            if len(entries)>=self.max_entries:break
        return WorkspaceEvidence("list",str(path.relative_to(self.root)),_digest(entries),entries=tuple(entries))
    def read(self,relative):
        path=self._path(relative)
        if not path.is_file():raise WorkspaceError("workspace target is not a file")
        if path.stat().st_size>self.max_file_bytes:raise WorkspaceError("file exceeds workspace size limit")
        try:text=path.read_text(encoding="utf-8")
        except UnicodeError as exc:raise WorkspaceError("unsupported or invalid UTF-8 content") from exc
        safe=_redact(text);safe=safe[:self.max_output_bytes]
        return WorkspaceEvidence("read",str(path.relative_to(self.root)),_digest({"path":str(path.relative_to(self.root)),"content":text}),safe,redacted=safe!=text)
    def write(self,relative,content):
        path=self._path(relative)
        if path.exists() and path.is_dir():raise WorkspaceError("workspace target is a directory")
        if not isinstance(content,str) or len(content.encode())>self.max_file_bytes:raise WorkspaceError("write content exceeds workspace size limit")
        if _SECRET.search(content):raise WorkspaceError("secret-like content is not permitted")
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding="utf-8",newline="")
        return WorkspaceEvidence("write",str(path.relative_to(self.root)),_digest({"path":str(path.relative_to(self.root)),"content":content}))
    def transform(self,relative,find,replace):
        evidence=self.read(relative);content=evidence.content
        if not isinstance(find,str) or not find:raise WorkspaceError("transform find text is required")
        updated=content.replace(find,str(replace))
        if updated==content:return evidence
        return self.write(relative,updated)
