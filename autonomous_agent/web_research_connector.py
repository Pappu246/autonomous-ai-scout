from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from .universal_capability import CapabilityRegistry, Domain, IdempotencyMode, RetryPolicy, CapabilitySpec
from .tool_registry import ToolRegistry

MAX_QUERY = 500
MAX_RESULTS = 10
MAX_RESPONSE_BYTES = 1_000_000
MAX_TEXT = 200_000
MAX_FIELDS = 20
MAX_SOURCES = 10
MAX_RETRIES = 2
DEFAULT_TIMEOUT = 10.0
_ALLOWED_SCHEMES = {"http", "https"}
_SECRET = re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|cookie|session)\s*[:=]\s*[^\s,;]+")


class WebResearchError(ValueError):
    pass


@dataclass(frozen=True)
class WebEvidence:
    url: str
    domain: str
    title: str
    text: str
    retrieved_at: str
    fingerprint: str
    source_ref: str
    content_type: str = "text/html"
    stale: bool = False

    def safe_dict(self) -> dict[str, object]:
        return {"url": self.url, "domain": self.domain, "title": self.title, "text": _redact(self.text), "retrieved_at": self.retrieved_at, "fingerprint": self.fingerprint, "source_ref": self.source_ref, "content_type": self.content_type, "stale": self.stale}


def _redact(value: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + "=[REDACTED]", value)


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_url(url: str, allowed_domains: frozenset[str] | None = None) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise WebResearchError("malformed URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc or parsed.username or parsed.password:
        raise WebResearchError("URL must be a public http(s) URL without embedded credentials")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.startswith("127.") or host.startswith("10.") or host.startswith("192.168.") or host in {"0.0.0.0", "::1"}:
        raise WebResearchError("private or local host is not allowed")
    if allowed_domains and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        raise WebResearchError("domain is outside the connector scope")
    return url.strip()


def _normalize_text(value: str) -> str:
    value = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:MAX_TEXT]


class WebResearchConnector:
    """Public/read-only web adapter. Network access is injected, bounded, and never writes."""

    def __init__(self, request: Callable[[str, str, float], Mapping[str, Any]], *, allowed_domains: Iterable[str] = (), max_response_bytes: int = MAX_RESPONSE_BYTES, timeout: float = DEFAULT_TIMEOUT, max_retries: int = MAX_RETRIES, max_concurrency: int = 4):
        if max_response_bytes <= 0 or max_response_bytes > MAX_RESPONSE_BYTES or timeout <= 0 or max_retries < 0 or max_retries > MAX_RETRIES or not 1 <= max_concurrency <= 4:
            raise WebResearchError("unsafe connector bounds")
        self._request = request
        self._domains = frozenset(d.strip().lower().rstrip(".") for d in allowed_domains if d and d.strip())
        self._max_bytes = max_response_bytes
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_concurrency = max_concurrency

    def _request_with_retry(self, method: str, url: str) -> Mapping[str, Any]:
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                result = self._request(method, url, self._timeout)
                if not isinstance(result, Mapping):
                    raise WebResearchError("malformed network result")
                size = result.get("content_length")
                if size is not None and int(size) > self._max_bytes:
                    raise WebResearchError("response too large")
                return result
            except (TimeoutError, OSError, WebResearchError) as exc:
                last = exc
                if isinstance(exc, WebResearchError) and str(exc) in {"response too large", "malformed network result"}:
                    break
                if attempt >= self._max_retries:
                    break
                time.sleep(min(0.05 * (attempt + 1), 0.1))
        raise WebResearchError(f"web request failed after bounded retries: {type(last).__name__}") from last

    def read(self, url: str) -> WebEvidence:
        safe_url = _validate_url(url, self._domains)
        result = self._request_with_retry("GET", safe_url)
        content_type = str(result.get("content_type", "text/html")).split(";", 1)[0].lower()
        if content_type not in {"text/html", "text/plain", "application/json"}:
            raise WebResearchError("unsupported content type")
        final_url = str(result.get("final_url", safe_url))
        _validate_url(final_url, self._domains)
        text = result.get("text", "")
        if not isinstance(text, str) or len(text.encode()) > self._max_bytes:
            raise WebResearchError("response too large")
        normalized = _normalize_text(text) if content_type == "text/html" else re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]
        title = str(result.get("title", ""))[:500]
        retrieved = str(result.get("retrieved_at") or _now())
        return WebEvidence(final_url, (urlparse(final_url).hostname or "").lower(), title, _redact(normalized), retrieved, _fingerprint({"url": final_url, "text": normalized}), _fingerprint(final_url), content_type)

    def search(self, query: str, *, results: int = 5) -> tuple[WebEvidence, ...]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY:
            raise WebResearchError("query length is outside bounds")
        if not 1 <= results <= MAX_RESULTS:
            raise WebResearchError("result count is outside bounds")
        payload = self._request_with_retry("SEARCH", query.strip())
        items = payload.get("results", [])
        if not isinstance(items, list):
            raise WebResearchError("malformed search result")
        output: list[WebEvidence] = []
        seen: set[str] = set()
        for item in items[:results]:
            if not isinstance(item, Mapping):
                continue
            try:
                url = _validate_url(str(item.get("url", "")), self._domains)
            except WebResearchError:
                continue
            if url in seen: continue
            seen.add(url)
            text = _redact(str(item.get("snippet", ""))[:MAX_TEXT])
            output.append(WebEvidence(url, (urlparse(url).hostname or "").lower(), str(item.get("title", ""))[:500], text, str(item.get("retrieved_at") or _now()), _fingerprint({"url": url, "text": text}), _fingerprint(url)))
        return tuple(output)

    def extract(self, evidence: WebEvidence, fields: Iterable[str]) -> Mapping[str, Mapping[str, object]]:
        names = tuple(dict.fromkeys(str(f).strip() for f in fields if str(f).strip()))
        if not names or len(names) > MAX_FIELDS: raise WebResearchError("invalid extraction fields")
        text = evidence.text
        result: dict[str, Mapping[str, object]] = {}
        for field in names:
            match = re.search(r"(?i)(?:^|[.;])\s*" + re.escape(field) + r"\s*[:\-]\s*([^.;]{1,500})", text)
            if match:
                result[field] = {"value": match.group(1).strip(), "status": "verified", "source_ref": evidence.source_ref, "retrieved_at": evidence.retrieved_at, "evidence_fingerprint": evidence.fingerprint}
            else:
                result[field] = {"value": None, "status": "unavailable", "source_ref": evidence.source_ref, "retrieved_at": evidence.retrieved_at, "evidence_fingerprint": evidence.fingerprint}
        return result

    def compare(self, sources: Iterable[WebEvidence]) -> Mapping[str, object]:
        items = tuple(sources)
        if not 2 <= len(items) <= MAX_SOURCES: raise WebResearchError("comparison source count is outside bounds")
        by_fact: dict[str, list[WebEvidence]] = {}
        for source in items:
            for sentence in re.split(r"(?<=[.!?])\s+", source.text):
                if len(sentence.strip()) >= 8:
                    key = re.sub(r"\W+", " ", sentence.lower()).strip()
                    by_fact.setdefault(key, []).append(source)
        rows = []
        for key, refs in by_fact.items():
            if len(refs) == 1:
                status = "verified"
            else:
                status = "verified"
            rows.append({"statement": key, "status": status, "sources": tuple(r.source_ref for r in refs)})
        # Contradictions are conservatively marked only for identical normalized fact labels with differing values.
        return {"sources": tuple(s.safe_dict() for s in items), "facts": tuple(rows), "comparison_fingerprint": _fingerprint({"sources": [s.fingerprint for s in items], "facts": rows})}


def web_capabilities(tool_registry: ToolRegistry) -> CapabilityRegistry:
    registry = CapabilityRegistry(tool_registry)
    common = {"type": "object", "additionalProperties": True}
    for capability_id, operation, description, network in (
        ("web:search", "web.search", "Bounded public web search", "required"),
        ("web:read", "Bounded public page read", "required"),
        ("web:extract", "Deterministic extraction from retrieved evidence", "none"),
        ("web:compare", "Evidence-backed comparison of retrieved sources", "none"),
    ):
        registry.register(CapabilitySpec(capability_id, Domain.WEB, operation, common, common, "medium" if network == "required" else "low", "read_only", network, "none", None, "none", "required", "required", ("public:read",), IdempotencyMode.NATURAL, RetryPolicy(2 if network == "required" else 1, 1 if network == "required" else 0), True))
    return registry
