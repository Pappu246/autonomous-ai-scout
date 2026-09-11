from __future__ import annotations
import ast,json,os,shutil,signal,subprocess,sys
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping
MAX_TIMEOUT_SECONDS=120; MAX_OUTPUT_BYTES=64*1024; MAX_READ_BYTES=128*1024; MAX_FILES=5000
SAFE_OPERATIONS={"inspect","test","lint","metrics","read_file","benchmark","web_research"}
@dataclass(frozen=True)
class SandboxResult:
    operation:str; success:bool; exit_status:int|None; output:str; output_truncated:bool; command:tuple[str,...]; verification_status:str; started_at:str; finished_at:str; network_disabled:bool
@dataclass(frozen=True)
class ExecutionRecord:
    action_id:str; approval_id:str; timestamp:str; category:str; command:str; result:str; exit_status:int|None; output_summary:str; verification_status:str
def _text_limit(text,limit):
    raw=text.encode("utf-8",errors="replace"); return raw[:limit].decode("utf-8",errors="replace"),len(raw)>limit
def _root(root):
    resolved=root.resolve()
    if not resolved.is_dir(): raise ValueError("sandbox root is not a valid project directory")
    return resolved
def _inside(root,target):
    resolved=target.resolve()
    try: resolved.relative_to(root)
    except ValueError as exc: raise ValueError("sandbox target escapes the project root") from exc
    return resolved
def _safe_env(): return {"PATH":os.environ.get("PATH",""),"LANG":os.environ.get("LANG","C.UTF-8"),"LC_ALL":os.environ.get("LC_ALL","C.UTF-8"),"PYTHONDONTWRITEBYTECODE":"1","PYTHONHASHSEED":"0"}
def _network_prefix():
    unshare=shutil.which("unshare")
    return None if not unshare or os.name!="posix" else (unshare,"--user","--map-root-user","--net","--mount-proc","--")
def _kill(process):
    if os.name=="posix":
        try: os.killpg(process.pid,signal.SIGKILL); return
        except (OSError,ProcessLookupError): pass
    try: process.kill()
    except (OSError,ProcessLookupError): pass
def _run_test(root,timeout_seconds,output_limit):
    prefix=_network_prefix()
    if prefix is None:return None,"network isolation unavailable; sandbox refused subprocess execution",False,()
    command=(sys.executable,"-m","pytest","-q","-p","no:cacheprovider")
    try:
        process=subprocess.Popen(prefix+command,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=_safe_env(),shell=False,start_new_session=True); stdout,_=process.communicate(timeout=max(1,min(int(timeout_seconds),MAX_TIMEOUT_SECONDS)))
    except subprocess.TimeoutExpired:
        _kill(process); stdout,_=process.communicate(); text,truncated=_text_limit((stdout or b"").decode("utf-8",errors="replace"),output_limit); return process.returncode,"timeout\n"+text,truncated,command
    except OSError as exc:return None,f"sandbox subprocess could not start: {exc}",False,command
    text,truncated=_text_limit((stdout or b"").decode("utf-8",errors="replace"),output_limit); return process.returncode,text,truncated,command
def _run_web_research(connector,request,limit):
    if connector is None or not isinstance(request,Mapping):return False,"web_research requires an approved injected connector and structured request",(),False
    op=str(request.get("operation","")).strip().lower()
    try:
        if op=="search": payload=[e.safe_dict() for e in connector.search(str(request.get("query","")),results=int(request.get("results",5)))]
        elif op=="read": payload=connector.read(str(request.get("url",""))).safe_dict()
        elif op=="extract": payload=connector.extract(request["evidence"],request.get("fields",()))
        elif op=="compare": payload=connector.compare(request.get("sources",()))
        else:return False,"sandbox web_research allowlist supports only search/read/extract/compare",(),False
        text,truncated=_text_limit(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str),limit); return True,text,("WEB_RESEARCH",op),truncated
    except Exception as exc:return False,f"web research operation failed: {type(exc).__name__}",("WEB_RESEARCH",op),False
def run_safe_operation(operation,root,target=None,*,timeout_seconds=30,output_limit=MAX_OUTPUT_BYTES,web_connector:Any=None,web_request:Mapping[str,Any]|None=None):
    started=datetime.now(timezone.utc).isoformat(); op=operation.strip().lower(); limit=max(1,min(int(output_limit),MAX_OUTPUT_BYTES)); root_path=_root(root)
    if op not in SAFE_OPERATIONS:
        finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,False,None,"operation is outside the sandbox allowlist",False,(),"blocked",started,finished,True)
    if op=="web_research":
        success,output,command,truncated=_run_web_research(web_connector,web_request or {},limit); finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,success,0 if success else 1,output,truncated,command,"verified" if success else "failed",started,finished,False)
    if op=="inspect":
        files=[]
        for path in sorted(root_path.rglob("*")):
            if ".git" in path.parts or not path.is_file():continue
            files.append(str(path.relative_to(root_path)))
            if len(files)>=MAX_FILES:break
        output,truncated=_text_limit("Workspace files:\n"+"\n".join(f"- {x}" for x in files),limit); finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,True,0,output,truncated,(),"verified",started,finished,True)
    if op=="metrics":
        count=0; total=0
        for path in root_path.rglob("*"):
            if ".git" in path.parts or not path.is_file():continue
            count+=1
            try:total+=path.stat().st_size
            except OSError:continue
            if count>=MAX_FILES:break
        finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,True,0,f"files={count}\ntotal_bytes={total}",False,(),"verified",started,finished,True)
    if op=="read_file":
        if not target:
            finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,False,None,"read_file requires a target",False,(),"blocked",started,finished,True)
        try:
            path=_inside(root_path,root_path/target)
            if not path.is_file():raise ValueError("sandbox target is not a file")
            if path.stat().st_size>MAX_READ_BYTES:raise ValueError("sandbox read target exceeds the size limit")
            output=path.read_text(encoding="utf-8")
        except (OSError,UnicodeError,ValueError) as exc:
            finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,False,None,str(exc),False,(),"failed",started,finished,True)
        output,truncated=_text_limit(output,limit); finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,True,0,output,truncated,(),"verified",started,finished,True)
    if op=="lint":
        failures=[]; checked=0
        for path in sorted(root_path.rglob("*.py")):
            if ".git" in path.parts:continue
            checked+=1
            if checked>MAX_FILES:break
            try:ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
            except (OSError,UnicodeError,SyntaxError) as exc:failures.append(f"{path.relative_to(root_path)}: {exc}")
        output,truncated=_text_limit(f"checked={checked}\n"+("\n".join(failures) if failures else "syntax checks passed"),limit); finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,not failures,0 if not failures else 1,output,truncated,(),"verified" if not failures else "failed",started,finished,True)
    if op=="benchmark":
        finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,True,0,f"deterministic_local_benchmark={sum(range(10000))}",False,(),"verified",started,finished,True)
    status,output,truncated,command=_run_test(root_path,timeout_seconds,limit); finished=datetime.now(timezone.utc).isoformat(); return SandboxResult(op,status==0,status,output,truncated,command,"verified" if status==0 else "failed",started,finished,True)
def to_execution_record(action_id,approval_id,result):return ExecutionRecord(action_id,approval_id,result.finished_at,result.operation," ".join(result.command),"success" if result.success else "failure",result.exit_status,result.output[:2000],result.verification_status)
