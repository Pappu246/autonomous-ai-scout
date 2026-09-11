from __future__ import annotations
import hashlib, html, json, re, time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

MAX_QUERY=500; MAX_RESULTS=10; MAX_RESPONSE_BYTES=1_000_000; MAX_TEXT=200_000; MAX_FIELDS=20; MAX_SOURCES=10; MAX_RETRIES=2
_SECRET=re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|cookie|session)\s*[:=]\s*[^\s,;]+")
class WebConnectorError(ValueError): pass
class WebResearchError(WebConnectorError): pass
@dataclass(frozen=True)
class WebEvidence:
    url:str; domain:str; title:str; text:str; retrieved_at:str; fingerprint:str; source_ref:str; content_type:str="text/html"; stale:bool=False
    def safe_dict(self): return {"url":self.url,"domain":self.domain,"title":self.title,"text":_redact(self.text),"retrieved_at":self.retrieved_at,"fingerprint":self.fingerprint,"source_ref":self.source_ref,"content_type":self.content_type,"stale":self.stale}
def _redact(v): return _SECRET.sub(lambda m:m.group(1)+"=[REDACTED]",str(v))
def _fingerprint(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str).encode()).hexdigest()
def _now(): return datetime.now(timezone.utc).isoformat()
def _parse_time(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except (TypeError,ValueError):return None
def _validate_url(url,domains=frozenset()):
    if not isinstance(url,str) or len(url.strip())>2048: raise WebConnectorError("malformed URL")
    p=urlparse(url.strip()); host=(p.hostname or "").lower().rstrip(".")
    if p.scheme not in {"http","https"} or not p.netloc or p.username or p.password or not host: raise WebConnectorError("only absolute public HTTP(S) URLs are allowed")
    if host=="localhost" or host.endswith(".localhost") or host.startswith(("127.","10.","192.168.")) or host in {"0.0.0.0","::1"}: raise WebConnectorError("private or local host is not allowed")
    if domains and not any(host==d or host.endswith("."+d) for d in domains): raise WebConnectorError("domain is outside the connector scope")
    return url.strip()
def _normalize(v):
    v=re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>"," ",v); v=re.sub(r"<[^>]+>"," ",v); return re.sub(r"\s+"," ",html.unescape(v)).strip()[:MAX_TEXT]
class WebResearchConnector:
    """Public/read-only connector with bounded transport supplied by the approved host."""
    def __init__(self,fetch:Callable[...,Mapping[str,Any]],*,allowed_domains:Iterable[str]=(),timeout_seconds=10,max_retries=MAX_RETRIES,max_concurrency=4,max_response_bytes=MAX_RESPONSE_BYTES,stale_after_seconds=86400):
        if not 1<=timeout_seconds<=30 or not 0<=max_retries<=MAX_RETRIES or not 1<=max_concurrency<=4 or not 1<=max_response_bytes<=MAX_RESPONSE_BYTES or not 60<=stale_after_seconds<=7*86400: raise WebConnectorError("unsafe connector bounds")
        self._fetch=fetch; self._domains=frozenset(x.strip().lower().rstrip(".") for x in allowed_domains if x.strip()); self._timeout=timeout_seconds; self._retries=max_retries; self._concurrency=max_concurrency; self._max_bytes=max_response_bytes; self._stale=stale_after_seconds
    def _request(self,method,url):
        last=None
        for attempt in range(self._retries+1):
            try:
                try: value=self._fetch(method,url,self._timeout)
                except TypeError: value=self._fetch(url,{"timeout":self._timeout})
                if not isinstance(value,Mapping): raise WebConnectorError("web response is not structured")
                if value.get("content_length") is not None and int(value["content_length"])>self._max_bytes: raise WebConnectorError("response too large")
                return value
            except (TimeoutError,OSError,WebConnectorError) as exc:
                last=exc
                if isinstance(exc,WebConnectorError) and str(exc) in {"response too large","web response is not structured"}: break
                if attempt<self._retries: time.sleep(min(.05*(attempt+1),.1))
        raise WebConnectorError(f"web request failed after bounded retries: {type(last).__name__}") from last
    def read(self,url):
        safe=_validate_url(url,self._domains); value=self._request("GET",safe); typ=str(value.get("content_type","text/html")).split(";",1)[0].lower()
        if typ not in {"text/html","text/plain","application/json"}: raise WebConnectorError("unsupported content type")
        final=str(value.get("final_url",safe)); _validate_url(final,self._domains); text=value.get("text","")
        if not isinstance(text,str) or len(text.encode())>self._max_bytes: raise WebConnectorError("response too large")
        normalized=_normalize(text) if typ=="text/html" else re.sub(r"\s+"," ",text).strip()[:MAX_TEXT]; retrieved=str(value.get("retrieved_at") or _now()); parsed=_parse_time(retrieved); stale=parsed is not None and datetime.now(timezone.utc)-parsed>timedelta(seconds=self._stale)
        return WebEvidence(final,(urlparse(final).hostname or "").lower(),str(value.get("title",""))[:500],_redact(normalized),retrieved,_fingerprint({"url":final,"text":normalized}),_fingerprint(final),typ,stale)
    def fetch(self,url,*,timeout_seconds=None):
        """Compatibility alias returning minimal safe evidence."""
        old=self._timeout
        if timeout_seconds is not None:
            if not 1<=timeout_seconds<=30: raise WebConnectorError("timeout is outside bounded safe limits")
            self._timeout=timeout_seconds
        try:
            e=self.read(url); return {"url":e.url,"status":200,"content_type":e.content_type,"text":e.text}
        finally:self._timeout=old
    def search(self,query,*,results=5):
        if not isinstance(query,str) or not 1<=len(query.strip())<=MAX_QUERY or not 1<=results<=MAX_RESULTS: raise WebConnectorError("query or result bounds exceeded")
        items=self._request("SEARCH",query.strip()).get("results",[])
        if not isinstance(items,list): raise WebConnectorError("malformed search result")
        out=[]; seen=set()
        for item in items[:results]:
            if not isinstance(item,Mapping): continue
            try:url=_validate_url(str(item.get("url","")),self._domains)
            except WebConnectorError:continue
            if url in seen:continue
            seen.add(url); text=_redact(str(item.get("snippet",""))[:MAX_TEXT]); retrieved=str(item.get("retrieved_at") or _now()); parsed=_parse_time(retrieved); stale=parsed is not None and datetime.now(timezone.utc)-parsed>timedelta(seconds=self._stale); out.append(WebEvidence(url,(urlparse(url).hostname or "").lower(),str(item.get("title",""))[:500],text,retrieved,_fingerprint({"url":url,"text":text}),_fingerprint(url),"text/html",stale))
        return tuple(out)
    def extract(self,evidence,fields):
        names=tuple(dict.fromkeys(str(x).strip() for x in fields if str(x).strip()))
        if not names or len(names)>MAX_FIELDS: raise WebConnectorError("invalid extraction fields")
        return {f:(lambda m:{"value":m.group(1).strip(),"status":"verified","source_ref":evidence.source_ref,"retrieved_at":evidence.retrieved_at,"evidence_fingerprint":evidence.fingerprint} if m else {"value":None,"status":"unavailable","source_ref":evidence.source_ref,"retrieved_at":evidence.retrieved_at,"evidence_fingerprint":evidence.fingerprint})(re.search(r"(?i)(?:^|[.;])\s*"+re.escape(f)+r"\s*[:\-]\s*([^.;]{1,500})",evidence.text)) for f in names}
    def compare(self,sources):
        items=tuple(sources)
        if not 2<=len(items)<=MAX_SOURCES: raise WebConnectorError("comparison source count exceeded")
        field_values={}; generic={}
        for s in items:
            for sentence in re.split(r"(?<=[.!?])\s+",s.text):
                clean=sentence.strip()
                match=re.match(r"^([A-Za-z][A-Za-z0-9 _-]{0,79})\s*[:\-]\s*([^.;!?]{1,500})[.!?]?$",clean)
                if match:
                    field=re.sub(r"\W+"," ",match.group(1).lower()).strip(); value=re.sub(r"\s+"," ",match.group(2)).strip().lower()
                    if field and value: field_values.setdefault(field,{}).setdefault(value,[]).append(s.source_ref)
                else:
                    key=re.sub(r"\W+"," ",clean.lower()).strip()
                    if len(key)>=8: generic.setdefault(key,[]).append(s.source_ref)
        facts=[]
        for field,values in sorted(field_values.items()):
            status="conflicting" if len(values)>1 else "verified"
            for value,refs in sorted(values.items()): facts.append({"statement":f"{field}: {value}","status":status,"sources":tuple(refs)})
        facts.extend({"statement":k,"status":"verified","sources":tuple(v)} for k,v in sorted(generic.items()))
        facts=tuple(facts)
        return {"sources":tuple(s.safe_dict() for s in items),"facts":facts,"comparison_fingerprint":_fingerprint({"sources":[s.fingerprint for s in items],"facts":facts})}
def record_web_evidence(memory,project,evidence,*,task_digest="",verification_status="verified"):
    from .cross_project_memory import MemoryEvent
    return memory.record(MemoryEvent(project,"web_evidence",evidence.fingerprint,verification_status,{"url":evidence.url,"domain":evidence.domain,"source_ref":evidence.source_ref,"retrieved_at":evidence.retrieved_at,"content_fingerprint":evidence.fingerprint,"task_digest":task_digest,"stale":evidence.stale}))
def meaningful_web_change(memory,project,evidence): return not memory.has(project=project,kind="web_evidence",fingerprint=evidence.fingerprint)
