from __future__ import annotations
from autonomous_agent.web_domain_connector import WebEvidence, WebResearchConnector, WebConnectorError, record_web_evidence, meaningful_web_change
from autonomous_agent.universal_capability import web_capabilities
from autonomous_agent.connector_registry import web_connector
from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import REGISTRY
SHA="a"*64

def response(method,url,timeout=10):
    if method=="SEARCH": return {"results":[{"title":"A","url":"https://example.com/a","snippet":"Version: 1.2.3.","retrieved_at":"2026-09-11T00:00:00+00:00"},{"title":"dup","url":"https://example.com/a","snippet":"duplicate"},{"title":"B","url":"https://example.org/b","snippet":"Version: 2.0."}]}
    return {"title":"Example","final_url":url,"content_type":"text/html","text":"<html><script>x</script> Version: 1.2.3. Status: stable.</html>","content_length":200,"retrieved_at":"2026-09-11T00:00:00+00:00"}

def test_search_success_and_dedup():
    r=WebResearchConnector(response,allowed_domains=["example.com","example.org"]).search("latest",results=5); assert len(r)==2 and r[0].domain=="example.com"
def test_read_success_normalizes_and_fingerprints():
    e=WebResearchConnector(response,allowed_domains=["example.com"]).read("https://example.com/a"); assert "script" not in e.text and e.fingerprint
def test_extract_and_compare():
    c=WebResearchConnector(response,allowed_domains=["example.com","example.org"]); e=c.read("https://example.com/a"); x=c.extract(e,["Version","Missing"]); assert x["Version"]["status"]=="verified" and x["Missing"]["status"]=="unavailable"; assert len(c.compare(c.search("x",results=3))["sources"])==2
def test_empty_results(): assert WebResearchConnector(lambda *a:{"results":[]}).search("x")==()
def test_bad_urls_and_scope():
    c=WebResearchConnector(response,allowed_domains=["example.com"])
    for u in ("not-url","file:///tmp/x","ftp://example.com/x","https://user:pass@example.com/x","http://127.0.0.1/x","https://other.example/x"):
        try:c.read(u); assert False
        except WebConnectorError:pass
def test_unsafe_redirect_and_content_type():
    def f(*a): return {"final_url":"https://evil.example/x","content_type":"text/html","text":"x","content_length":1}
    try:WebResearchConnector(f,allowed_domains=["example.com"]).read("https://example.com/x"); assert False
    except WebConnectorError:pass
    def g(*a): return {"final_url":"https://example.com/x","content_type":"application/octet-stream","text":"x","content_length":1}
    try:WebResearchConnector(g,allowed_domains=["example.com"]).read("https://example.com/x"); assert False
    except WebConnectorError:pass
def test_oversize_timeout_and_retry():
    def big(*a): return {"content_length":2_000_000}
    try:WebResearchConnector(big,allowed_domains=["example.com"]).read("https://example.com/x"); assert False
    except WebConnectorError:pass
    calls=[]
    def slow(*a):calls.append(1);raise TimeoutError()
    try:WebResearchConnector(slow,max_retries=2).read("https://example.com/x");assert False
    except WebConnectorError:assert len(calls)==3
def test_stale_and_deterministic_fingerprint():
    c=WebResearchConnector(response,allowed_domains=["example.com"],stale_after_seconds=60); a=c.read("https://example.com/a"); b=c.read("https://example.com/a"); assert a.fingerprint==b.fingerprint and a.stale

def test_secret_safe_evidence_and_memory():
    def f(*a):return {"final_url":"https://example.com/x","content_type":"text/plain","text":"api_key=supersecret public fact","content_length":50}
    e=WebResearchConnector(f,allowed_domains=["example.com"]).read("https://example.com/x"); assert "supersecret" not in e.text
    class Memory:
        def __init__(self):self.items=[]
        def record(self,event):self.items.append(event);return True
        def has(self,**kwargs):return False
    m=Memory(); assert record_web_evidence(m,"project-a",e); assert meaningful_web_change(m,"project-a",e)

def test_memory_failure_does_not_change_evidence():
    class Broken:
        def record(self,*a):raise OSError("storage")
    e=WebResearchConnector(response,allowed_domains=["example.com"]).read("https://example.com/a")
    try:record_web_evidence(Broken(),"p",e);assert False
    except OSError:pass
    assert e.fingerprint
def test_connector_capabilities_policy_and_disabled():
    reg=web_capabilities(REGISTRY); assert {x.capability_id for x in reg.list()}=={"web:compare","web:extract","web:read","web:search"}; assert reg.authorize("web:search",(Capability.WEB_RESEARCH.value,)).allowed; assert not reg.authorize("web:search",()).allowed; assert web_connector().resolve_tool("web_research","web.compare")
def test_bounded_concurrency_and_capability_mismatch():
    try:WebResearchConnector(response,max_concurrency=5);assert False
    except WebConnectorError:pass
    assert not web_capabilities(REGISTRY).authorize("web:search",(Capability.INSPECT.value,)).allowed
def test_contradictory_sources_are_not_invented_as_facts():
    c=WebResearchConnector(response,allowed_domains=["a.example","b.example"]); a=WebEvidence("https://a.example","a.example","a","Status: stable.","2026-09-11T00:00:00Z",SHA,"a"); b=WebEvidence("https://b.example","b.example","b","Status: unstable.","2026-09-11T00:00:00Z",SHA,"b"); out=c.compare((a,b)); assert all(x["status"] in {"verified","conflicting","unavailable","inferred"} for x in out["facts"])
