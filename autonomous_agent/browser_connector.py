from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse


MAX_URL_LENGTH = 2048
MAX_TEXT_LENGTH = 16_384
DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 30
ALLOWED_SCHEMES = {"https"}
BLOCKED_SCHEMES = {"file", "javascript", "data", "ftp"}


class BrowserTransport(Protocol):
    def __call__(self, action: str, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BrowserResult:
    action: str
    url: str | None
    title: str = ""
    text: str = ""
    links: tuple[dict[str, str], ...] = ()
    status_code: int | None = None
    verification_status: str = "verified"

    def safe_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "url": self.url,
            "title": self.title[:MAX_TEXT_LENGTH],
            "text": self.text[:MAX_TEXT_LENGTH],
            "links": [dict(link) for link in self.links[:100]],
            "status_code": self.status_code,
            "verification_status": self.verification_status,
        }


def validate_browser_url(url: str, allowed_hosts: frozenset[str]) -> str:
    if not isinstance(url, str) or len(url) == 0 or len(url) > MAX_URL_LENGTH:
        raise ValueError("browser URL is invalid or too long")
    parsed = urlparse(url)
    if parsed.scheme.lower() in BLOCKED_SCHEMES or parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError("browser URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("browser URL must contain only a hostname and path")
    host = parsed.hostname.lower().rstrip(".")
    if host not in allowed_hosts:
        raise ValueError("browser host is not explicitly allowed")
    if parsed.port not in (None, 443):
        raise ValueError("browser URL must use the default HTTPS port")
    return url


class ControlledBrowser:
    """Bounded browser abstraction with no arbitrary navigation or script execution."""

    def __init__(self, allowed_hosts: set[str] | frozenset[str], transport: BrowserTransport):
        normalized = frozenset(host.strip().lower().rstrip(".") for host in allowed_hosts if host.strip())
        if not normalized:
            raise ValueError("explicit browser host allowlist is required")
        self._allowed_hosts = normalized
        self._transport = transport

    def open(self, url: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> BrowserResult:
        timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
        safe_url = validate_browser_url(url, self._allowed_hosts)
        raw = self._transport("open", {"url": safe_url, "timeout_seconds": timeout})
        return self._result("open", raw, safe_url)

    def click(self, url: str, selector: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> BrowserResult:
        if not isinstance(selector, str) or not selector.strip() or len(selector) > 512:
            raise ValueError("browser selector is invalid")
        timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
        safe_url = validate_browser_url(url, self._allowed_hosts)
        raw = self._transport("click", {"url": safe_url, "selector": selector.strip(), "timeout_seconds": timeout})
        return self._result("click", raw, safe_url)

    def extract(self, url: str, fields: tuple[str, ...] = ()) -> BrowserResult:
        safe_url = validate_browser_url(url, self._allowed_hosts)
        clean_fields = tuple(field.strip()[:128] for field in fields if field.strip())[:20]
        raw = self._transport("extract", {"url": safe_url, "fields": clean_fields})
        return self._result("extract", raw, safe_url)

    @staticmethod
    def _result(action: str, raw: Mapping[str, Any], url: str) -> BrowserResult:
        title = str(raw.get("title", ""))[:MAX_TEXT_LENGTH]
        text = str(raw.get("text", ""))[:MAX_TEXT_LENGTH]
        status = raw.get("status_code")
        links_raw = raw.get("links", ())
        links: list[dict[str, str]] = []
        if isinstance(links_raw, (list, tuple)):
            for item in links_raw[:100]:
                if isinstance(item, Mapping):
                    href = str(item.get("href", ""))[:MAX_URL_LENGTH]
                    label = str(item.get("label", ""))[:512]
                    links.append({"href": href, "label": label})
        return BrowserResult(action, url, title, text, tuple(links), int(status) if isinstance(status, int) else None)


def browser_transport_from_mapping(responses: Mapping[str, Mapping[str, Any]]) -> Callable[[str, Mapping[str, Any]], Mapping[str, Any]]:
    """Small deterministic transport helper for tests; never launches a browser."""
    def transport(action: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        key = f"{action}:{request.get('url', '')}"
        return responses.get(key, responses.get(action, {}))

    return transport
