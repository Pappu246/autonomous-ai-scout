from autonomous_agent.knowledge_acquisition import KnowledgeAcquirer
from autonomous_agent.web_domain_connector import WebEvidence


class DemoWeb:
    def search(self, query, *, results):
        return (
            WebEvidence("https://example.com/a", "example.com", "Source A", "Price: 10. Feature: alpha.", "2026-09-23T00:00:00+00:00", "fp-a", "src-a"),
            WebEvidence("https://example.com/b", "example.com", "Source B", "Price: 12. Feature: beta.", "2026-09-23T00:00:00+00:00", "fp-b", "src-b"),
        )[:results]

    def read(self, url):
        return self.search("demo", results=2)[0] if url.endswith("/a") else self.search("demo", results=2)[1]

    def extract(self, evidence, fields):
        return {field: {"value": "10", "status": "verified", "source_ref": evidence.source_ref, "retrieved_at": evidence.retrieved_at, "evidence_fingerprint": evidence.fingerprint} for field in fields}

    def compare(self, sources):
        return {"facts": ({"statement": "price conflict", "status": "conflicting", "sources": tuple(item.source_ref for item in sources)},), "comparison_fingerprint": "demo-comparison"}


def main() -> int:
    packet = KnowledgeAcquirer(DemoWeb()).collect("compare examples", results=2, fields=("Price",))
    print("N24 Web Research + Knowledge Acquisition demo")
    print(f"sources: {packet.source_count}")
    print(f"source_refs: {[source.source_ref for source in packet.sources]}")
    print(f"facts: {packet.facts}")
    print(f"fingerprint: {packet.fingerprint}")
    return 0 if packet.source_count == 2 and packet.fingerprint == "demo-comparison" else 1


if __name__ == "__main__":
    raise SystemExit(main())
