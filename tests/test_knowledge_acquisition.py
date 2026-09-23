from autonomous_agent.knowledge_acquisition import KnowledgeAcquirer
from autonomous_agent.web_domain_connector import WebEvidence


class FakeWeb:
    def search(self, query, *, results):
        return (
            WebEvidence("https://a.example", "a.example", "A", "Price: 10. Feature: alpha.", "2026-09-23T00:00:00+00:00", "fp-a", "src-a"),
            WebEvidence("https://b.example", "b.example", "B", "Price: 12. Feature: beta.", "2026-09-23T00:00:00+00:00", "fp-b", "src-b"),
        )[:results]

    def read(self, url):
        return self.search("x", results=2)[0] if url.startswith("https://a") else self.search("x", results=2)[1]

    def extract(self, evidence, fields):
        return {field: {"value": "10" if field == "Price" else None, "status": "verified", "source_ref": evidence.source_ref, "retrieved_at": evidence.retrieved_at, "evidence_fingerprint": evidence.fingerprint} for field in fields}

    def compare(self, sources):
        return {"facts": ({"statement": "price conflict", "status": "conflicting", "sources": ("src-a", "src-b")},), "comparison_fingerprint": "comparison-fp"}


def test_knowledge_acquirer_collects_sources_and_provenance():
    packet = KnowledgeAcquirer(FakeWeb()).collect("compare examples", results=2, fields=("Price",))
    assert packet.source_count == 2
    assert packet.sources[0].source_ref == "src-a"
    assert any(fact.get("status") == "verified" and fact.get("field") == "Price" for fact in packet.facts)
    assert any(fact.get("status") == "conflicting" for fact in packet.facts)
    assert packet.fingerprint == "comparison-fp"


def test_knowledge_acquirer_survives_one_source_read_failure():
    class Partial(FakeWeb):
        def read(self, url):
            if url.startswith("https://b"):
                raise OSError("source unavailable")
            return super().read(url)

    packet = KnowledgeAcquirer(Partial()).collect("topic", results=2)
    assert packet.source_count == 2
    assert packet.sources[1].source_ref == "src-b"


def test_knowledge_acquirer_handles_no_results():
    class Empty(FakeWeb):
        def search(self, query, *, results):
            return ()

    packet = KnowledgeAcquirer(Empty()).collect("nothing", results=2)
    assert packet.source_count == 0
    assert packet.sources == ()
    assert packet.facts == ()


def test_knowledge_acquirer_compares_sources_only_once():
    class Counting(FakeWeb):
        def __init__(self):
            self.compare_calls = 0
        def compare(self, sources):
            self.compare_calls += 1
            return super().compare(sources)

    web = Counting()
    packet = KnowledgeAcquirer(web).collect("compare examples", results=2)
    assert packet.fingerprint == "comparison-fp"
    assert web.compare_calls == 1
