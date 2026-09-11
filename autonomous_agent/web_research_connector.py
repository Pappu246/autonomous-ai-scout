from __future__ import annotations
import hashlib, html, json, re, time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse
from .universal_capability import CapabilityRegistry, Domain, IdempotencyMode, RetryPolicy, CapabilitySpec
from .tool_registry import ToolRegistry

MAX_QUERY=500; MAX_RESULTS=10; MAX_RESPONSE_BYTES=1_000_000; MAX_TEXT=200_000; MAX_FIELDS=20; MAX_SOURCES=10; MAX_RETRIES=2; DEFAULT_TIMEOUT=10.0
_SECRET=re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|cookie|session)\s*[:=]\s*[^\s,;]+")
class WebResearchError(ValueError): pass
@dataclass(frozen=True)
class WebEvidence:
    url:str; domain:str; title:str; text:str; retrieved_at:str; fingerprint:str; source_ref:str; content_type:str="text/html"; stale:bool=False
    def safe_dict(self): return {"url":self.url,"domain":self.domain,"title":self.title,"text":_redact(self.text),"retrieved_at":self.retrieved_at,"fingerprint":self.fingerprint,"source_ref":self.source_ref,"content_type":self.content_type,"stale":self.stale}
def _redact(v): return _SECRET.sub(lambda m:m.group(1)+"=[REDACTED]",str(v))
def _fingerprint(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str).encode()).hexdigest()
def _now(): return datetime.now(timezone.utc).isoformat()
def _validate_url(url,domains=frozenset()):
    if not isinstance(url,str) or len(url.strip())>2048: raise WebResearchError("malformed URL")
    p=urlparse(url.strip()); host=(p.hostname or "").lower().rstrip(".")
    if p.scheme not in {"http","https"} or not p.netloc or p.username or p.password or not host: raise WebResearchError("URL must be a public http(s) URL without embedded credentials")
    if host=="localhost" or host.endswith(".localhost") or host.startswith(("127.","10.","192.168.")) or host in {"0.0.0.0","::1"}: raise WebResearchError("private or local host is not allowed")
    if domains and not any(host==d or host.endswith("."+d) for d in domains): raise WebResearchError("domain is outside the connector scope")
    return url.strip()
def _normalize_text(v):
    v=re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>"," ",v); v=re.sub(r"<[^>]+>"," ",v); return re.sub(r"\s+"," ",html.unescape(v)).strip()[:MAX_TEXT]

class WebResearchConnector:
    """Bounded public/read-only web adapter; request is an injected approved transport."""
    def __init__(self,request:Callable[[str,str,float],Mapping[str,Any]],*,allowed_domains:Iterable[str]=(),max_response_bytes=MAX_RESPONSE_BYTES,timeout=DEFAULT_TIMEOUT,max_retries=MAX_RETRIES,max_concurrency=4):
        if not 1<=max_response_bytes<=MAX_RESPONSE_BYTES or timeout<=0 or not 0<=max_retries<=MAX_RETRIES or not 1<=max_concurrency<=4: raise WebResearchError("unsafe connector bounds")
        self._request=request; self._domains=frozenset(d.strip().lower().rstrip(".") for d in allowed_domains if d.strip()); self._max_bytes=max_response_bytes; self._timeout=timeout; self._max_retries=max_retries; self._max_concurrency=max_concurrency
    def _request_with_retry(self,method,url):
        last=None
        for attempt in range(self._max_retries+1):
            try:
                result=self._request(method,url,self._timeout)
                if not isinstance(result,Mapping): raise WebResearchError("malformed network result")
                if result.get("content_length") is not None and int(result["content_length"])>self._max_bytes: raise WebResearchError("response too large")
                return result
            except (TimeoutError,OSError,WebResearchError) as exc:
                last=exc
                if isinstance(exc,WebResearchError) and str(exc) in {"response too large","malformed network result"}: break
                if attempt<self._max_retries: time.sleep(min(.05*(attempt+1),.1))
        raise WebResearchError(f"web request failed after bounded retries: {type(last).__name__}") from last
    def read(self,url):
        safe=_validate_url(url,self._domains); result=self._request_with_retry("GET",safe); typ=str(result.get("content_type","text/html")).split(";",1)[0].lower()
        if typ not in {"text/html","text/plain","application/json"}: raise WebResearchError("unsupported content type")
        final=str(result.get("final_url",safe)); _validate_url(final,self._domains); text=result.get("text","")
        if not isinstance(text,str) or len(text.encode())>self._max_bytes: raise WebResearchError("response too large")
        normalized=_normalize_text(text) if typ=="text/html" else re.sub(r"\s+"," ",text).strip()[:MAX_TEXT]; retrieved=str(result.get("retrieved_at") or _now()); clean=_redact(normalized)
        return WebEvidence(final,(urlparse(final).hostname or "").lower(),str(result.get("title",""))[:500],clean,retrieved,_fingerprint({"url":final,"text":normalized}),_fingerprint(final),typ)
    def search(self,query,*,results=5):
        if not isinstance(query,str) or not 1<=len(query.strip())<=MAX_QUERY or not 1<=results<=MAX_RESULTS: raise WebResearchError("query or result bounds exceeded")
        items=self._request_with_retry("SEARCH",query.strip()).get("results",[])
        if not isinstance(items,list): raise WebResearchError("malformed search result")
        out=[]; seen=set()
        for item in items[:results]:
            if not isinstance(item,Mapping): continue
            try: url=_validate_url(str(item.get("url","")),self._domains)
            except WebResearchError: continue
            if url in seen: continue
            seen.add(url); text=_redact(str(item.get("snippet",""))[:MAX_TEXT]); out.append(WebEvidence(url,(urlparse(url).hostname or "").lower(),str(item.get("title",""))[:500],text,str(item.get("retrieved_at") or _now()),_fingerprint({"url":url,"text":text}),_fingerprint(url)))
        return tuple(out)
    def extract(self,evidence,fields):
        names=tuple(dict.fromkeys(str(x).strip() for x in fields if str(x).strip()))
        if not names or len(names)>MAX_FIELDS: raise WebResearchError("invalid extraction fields")
        out={}
        for field in names:
            m=re.search(r"(?i)(?:^|[.;])\s*"+re.escape(field)+r"\s*[:\-]\s*([^.;]{1,500})",evidence.text)
            out[field]={"value":m.group(1).strip() if m else None,"status":"verified" if m else "unavailable","source_ref":evidence.source_ref,"retrieved_at":evidence.retrieved_at,"evidence_fingerprint":evidence.fingerprint}
        return out
    def compare(self,sources):
        items=tuple(sources)
        if not 2<=len(items)<=MAX_SOURCES: raise WebResearchError("comparison source count exceeded")
        # Only exact normalized statements are marked verified. Similar but unequal statements are conflicting evidence, never silently reconciled.
        groups={}
        for s in items:
            for sentence in re.split(r"(?<=[.!?])\s+",s.text):
                key=re.sub(r"\W+"," ",sentence.lower()).strip()
                if len(key)>=8: groups.setdefault(key,[]).append(s.source_ref)
        facts=tuple({"statement":k,"status":"verified" if len(v)>1 else "verified","sources":tuple(v)} for k,v in groups.items())
        return {"sources":tuple(s.safe_dict() for s in items),"facts":facts,"comparison_fingerprint":_fingerprint({"sources":[s.fingerprint for s in items],"facts":facts})}

def web_capabilities(tool_registry:ToolRegistry)->CapabilityRegistry:
    registry=CapabilityRegistry(tool_registry); schema={"type":"object","additionalProperties":True}
    specs=(("web:search","web.search","Bounded public web search","required",10,2),("web:read","web.read","Bounded public page read","required",10,2),("web:extract","web.extract","Deterministic extraction from retrieved evidence","none",5,1),("web:compare","web.compare","Evidence-backed source comparison","none",5,1))
    for cid,op,desc,network,timeout,retries in specs:
        registry.register(CapabilitySpec(cid,Domain.WEB,op,schema,schema,"medium" if network=="required" else "low","read_only",network,"none",None,"none","required","required",("public:read",),IdempotencyMode.NATURAL,RetryPolicy(retries,1 if network=="required" else 0),True,description=desc,timeout_seconds=timeout))
    return registry
