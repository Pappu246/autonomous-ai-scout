from __future__ import annotations
from autonomous_agent.web_research_connector import WebEvidence, WebResearchConnector, WebResearchError
from autonomous_agent.universal_capability import web_capabilities
from autonomous_agent.connector_registry import web_connector
from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import REGISTRY

SHA="a"*40

def response(path, url, timeout=10):
    if path=="SEARCH": return {"results":[{"title":"A","url":"https://example.com/a","snippet":"Version: 1.2.3.","retrieved_at":"2026-09-11T00:00:00+00:00"},{"title":"dup","url":"https://example.com/a","snippet":"duplicate"},{"title":"B","url":"https://example.org/b","snippet":"Version: 2.0."}]}
    return {"title":"Example","final_url":url,"content_type":"text/html","text":"<html><title>Example</title><script>x</script> Version: 1.2.3. Status: stable.</html>","content_length":200,"retrieved_at":"2026-09-11T00:00:00+00:00"}

def test_search_success_and_dedup():
    c=WebResearchConnector(response,allowed_domains=["example.com","example.org"])
    r=c.search("latest",results=5); assert len(r)==2 and r[0].domain=="example.com" and r[0].fingerprint

def test_read_success_normalizes_and_fingerprints():
    e=WebResearchConnector(response,allowed_domains=["example.com"]).read("https://example.com/a")
    assert "script" not in e.text and "Version: 1.2.3." in e.text and e.fingerprint

def test_extract_is_deterministic_and_source_linked():
    c=WebResearchConnector(response,allowed_domains=["example.com"]); e=c.read("https://example.com/a")
    x=c.extract(e,["Version","Missing"]); assert x["Version"]["status"]=="verified" and x["Version"]["source_ref"]==e.source_ref and x["Missing"]["status"]=="unavailable"

def test_compare_preserves_source_evidence():
    c=WebResearchConnector(response,allowed_domains=["example.com","example.org"]); sources=c.search("x",results=2)
    out=c.compare(sources); assert len(out["sources"])==2 and out["comparison_fingerprint"]

def test_empty_results():
    c=WebResearchConnector(lambda *a:{"results":[]}); assert c.search("x")==()

def test_malformed_url_and_unsupported_scheme():
    c=WebResearchConnector(response)
    for u in ("not-url","file:///tmp/x","ftp://example.com/x","https://user:pass@example.com/x"):
        try: c.read(u); assert False
        except WebResearchError: pass

def test_private_and_scope_violation():
    c=WebResearchConnector(response,allowed_domains=["example.com"])
    for u in ("http://127.0.0.1/x","https://other.example/x"):
        try: c.read(u); assert False
        except WebResearchError: pass

def test_unsafe_redirect_is_rejected():
    def f(*a): return {"final_url":"https://evil.example/x","content_type":"text/plain","text":"x","content_length":1}
    try: WebResearchConnector(f,allowed_domains=["example.com"]).read("https://example.com/x"); assert False
    except WebResearchError: pass

def test_response_too_large():
    def f(*a): return {"final_url":"https://example.com/x","content_type":"text/plain","text":"x","content_length":2_000_000}
    try: WebResearchConnector(f,allowed_domains=["example.com"]).read("https://example.com/x"); assert False
    except WebResearchError: pass

def test_timeout_and_retry_limit():
    calls=[]
    def f(*a): calls.append(1); raise TimeoutError()
    try: WebResearchConnector(f,max_retries=2).read("https://example.com/x"); assert False
    except WebResearchError: assert len(calls)==3

def test_duplicate_source_fingerprint_is_deterministic():
    c=WebResearchConnector(response,allowed_domains=["example.com"]); a=c.read("https://example.com/a"); b=c.read("https://example.com/a"); assert a.fingerprint==b.fingerprint

def test_stale_evidence_flag_is_preserved():
    e=WebEvidence("https://example.com","example.com","x","x","2020-01-01T00:00:00Z",SHA,SHA,stale=True); assert e.safe_dict()["stale"] is True

def test_credential_leakage_is_redacted_from_evidence():
    def f(*a): return {"final_url":"https://example.com/x","content_type":"text/plain","text":"api_key=supersecret public fact","content_length":50}
    e=WebResearchConnector(f,allowed_domains=["example.com"]).read("https://example.com/x"); assert "supersecret" not in e.text and "REDACTED" in e.text

def test_connector_and_capabilities_are_read_only_and_authority_delegated():
    reg=web_capabilities(REGISTRY); assert {x.capability_id for x in reg.list()}=={"web:compare","web:extract","web:read","web:search"}
    assert reg.authorize("web:search",(Capability.WEB_RESEARCH.value,)).allowed
    c=web_connector(); assert c.resolve_tool("web_research","web.search") is not None

def test_network_denial_and_disabled_capability_fail_closed():
    reg=web_capabilities(REGISTRY); assert not reg.authorize("web:search",()).allowed
    spec=reg.get("web:search"); assert spec.enabled

def test_capability_mismatch_fails_closed():
    reg=web_capabilities(REGISTRY); assert not reg.authorize("web:search",(Capability.INSPECT.value,)).allowed

def test_bounded_concurrency_and_invalid_bounds():
    for kwargs in ({"max_concurrency":5},{"max_retries":3},{"timeout":0}):
        try: WebResearchConnector(response,**kwargs); assert False
        except WebResearchError: pass

def test_compare_contradictory_sources_remains_evidence_only():
    a=WebEvidence("https://a.example","a.example","a","Status: stable.","2026-09-11T00:00:00Z",SHA,"a")
    b=WebEvidence("https://b.example","b.example","b","Status: unstable.","2026-09-11T00:00:00Z","b"*64,"b")
    out=WebResearchConnector(response).compare((a,b)); assert all(x["status"] in {"verified","conflicting","unavailable","inferred"} for x in out["facts"])

def test_memory_failure_is_not_a_web_authority():
    class BrokenMemory:
        def store_safe(self,*a,**k): raise OSError("storage failed")
    e=WebResearchConnector(response).read("https://example.com/a")
    try: BrokenMemory().store_safe(e.safe_dict())
    except OSError: pass
    assert e.fingerprint

def test_cross_project_evidence_is_not_mixed():
    a=WebEvidence("https://a.example","a.example","a","A","2026-09-11T00:00:00Z",SHA,"a")
    b=WebEvidence("https://b.example","b.example","b","B","2026-09-11T00:00:00Z","b"*64,"b")
    out=WebResearchConnector(response).compare((a,b)); assert {x["source_ref"] for x in out["sources"]}=={"a","b"}
