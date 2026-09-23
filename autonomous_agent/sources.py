from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx


@dataclass(frozen=True)
class SourceCheck:
    url: str
    reachable: bool
    title: str = ""
    text: str = ""
    digest: str = ""


_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_MAX_SOURCE_BYTES = 300_000
_MAX_REDIRECTS = 3


def _validate_source_url(url: str) -> str:
    if not isinstance(url, str) or len(url.strip()) > 2048:
        raise ValueError("source URL is invalid")
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise ValueError("only absolute HTTPS source URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL userinfo is not allowed")
    if parsed.port not in (None, 443):
        raise ValueError("only the default HTTPS source port is allowed")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("source hostname could not be resolved") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    if not addresses:
        raise ValueError("source hostname resolved to no addresses")
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise ValueError("source hostname resolved to a non-public address")
    return url.strip()


def fetch_source(url: str) -> SourceCheck:
    try:
        current = _validate_source_url(url)
        original_host = (urlsplit(current).hostname or "").lower().rstrip(".")
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False, headers={"User-Agent": "autonomous-ai-scout/0.2"}) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                response = client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return SourceCheck(url=url, reachable=False)
                    redirected = _validate_source_url(urljoin(current, location))
                    redirected_host = (urlsplit(redirected).hostname or "").lower().rstrip(".")
                    if redirected_host != original_host:
                        return SourceCheck(url=url, reachable=False)
                    current = redirected
                    continue
                response.raise_for_status()
                if len(response.content) > _MAX_SOURCE_BYTES:
                    return SourceCheck(url=url, reachable=False)
                text = response.content.decode(response.encoding or "utf-8", errors="replace")[:_MAX_SOURCE_BYTES]
                title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
                title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
                digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
                return SourceCheck(url=url, reachable=True, title=title[:500], text=text, digest=digest)
    except (httpx.HTTPError, UnicodeError, OSError, ValueError):
        return SourceCheck(url=str(url), reachable=False)
    return SourceCheck(url=str(url), reachable=False)


def source_has_free_signal(check: SourceCheck) -> bool:
    if not check.reachable:
        return False
    lowered = re.sub(r"\s+", " ", check.text.lower())
    positive = ("free tier", "free usage", "free credits", "free of charge", "free plan")
    negative = ("paid only", "no free tier", "payment required")
    return any(term in lowered for term in positive) and not any(term in lowered for term in negative)
