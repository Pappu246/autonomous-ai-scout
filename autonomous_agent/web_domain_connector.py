from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urlparse


class WebConnectorError(ValueError):
    pass


class WebResearchConnector:
    """Bounded web-read adapter; it never creates authority or executes writes."""

    def __init__(self, fetch: Callable[[str, Mapping[str, Any] | None], Any]):
        self._fetch = fetch

    @staticmethod
    def _url(url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise WebConnectorError("URL is required")
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebConnectorError("only absolute HTTP(S) URLs are allowed")
        if parsed.username or parsed.password:
            raise WebConnectorError("URL credentials are not allowed")
        return url.strip()

    def fetch(self, url: str, *, timeout_seconds: int = 10) -> Mapping[str, Any]:
        url = self._url(url)
        if not 1 <= timeout_seconds <= 30:
            raise WebConnectorError("timeout is outside bounded safe limits")
        value = self._fetch(url, {"timeout": timeout_seconds})
        if not isinstance(value, Mapping):
            raise WebConnectorError("web response is not structured")
        return {"url": url, "status": value.get("status"), "content_type": value.get("content_type"), "text": value.get("text", "")}
