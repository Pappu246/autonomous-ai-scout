from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin, urlsplit

MAX_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_HEADERS = 40
MAX_HEADER_BYTES = 8 * 1024
MAX_REDIRECTS = 3
MAX_RETRIES = 2
SAFE_METHODS = frozenset({"GET", "HEAD"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
BLOCKED_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})
SECRET_RE = re.compile(r"(?i)(bearer\s+|api[_-]?key\s*=\s*|password\s*=\s*|secret\s*=\s*)[^\s,;&]+")


class RestConnectorError(ValueError):
    pass


class RestTransport(Protocol):
    def request(self, method: str, url: str, *, headers: Mapping[str, str], body: bytes, timeout: float) -> "RestResponse": ...


@dataclass(frozen=True)
class RestResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def safe_dict(self) -> dict[str, Any]:
        body = self.body[:MAX_RESPONSE_BYTES]
        text = body.decode("utf-8", errors="replace")
        return {
            "status_code": int(self.status_code),
            "headers": _redact_headers(self.headers),
            "body": _redact_text(text),
            "url": self.url,
        }


@dataclass(frozen=True)
class RestRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes = b""
    credential_ref: str | None = None
    idempotency_key: str | None = None


def _redact_text(value: str) -> str:
    return SECRET_RE.sub(r"\1[REDACTED]", value)


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        out[str(key)] = "[REDACTED]" if str(key).lower() in BLOCKED_HEADERS else _redact_text(str(value))
    return out


def deterministic_idempotency_key(method: str, url: str, body: bytes) -> str:
    return hashlib.sha256(method.upper().encode() + b"\0" + url.encode() + b"\0" + body).hexdigest()


def _is_public_address(address: ipaddress._BaseAddress) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RestConnectorError("host DNS resolution failed") from exc
        addresses: list[ipaddress._BaseAddress] = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        return sorted(set(addresses), key=str)


def validate_public_https_url(url: str, allowed_hosts: frozenset[str], *, resolve_dns: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise RestConnectorError("URL is invalid or exceeds the length limit")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise RestConnectorError("only HTTPS URLs with a host are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise RestConnectorError("userinfo in URLs is not allowed")
    if host not in allowed_hosts:
        raise RestConnectorError("host is not explicitly allowlisted")
    if parsed.port not in (None, 443):
        raise RestConnectorError("only the default HTTPS port is allowed")
    addresses = _resolve_host_ips(host) if (resolve_dns or _looks_like_ip(host)) else ()
    if any(not _is_public_address(address) for address in addresses):
        raise RestConnectorError("resolved host address is not publicly routable")
    return url


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def validate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping) or len(headers) > MAX_HEADERS:
        raise RestConnectorError("headers exceed the allowed count")
    out: dict[str, str] = {}
    total = 0
    for key, value in headers.items():
        name = str(key).strip()
        val = str(value)
        if not name or any(ord(ch) < 32 or ord(ch) == 127 for ch in name + val):
            raise RestConnectorError("invalid header characters")
        if name.lower() in BLOCKED_HEADERS:
            raise RestConnectorError("credential-bearing headers must use credential references")
        total += len(name.encode()) + len(val.encode())
        if total > MAX_HEADER_BYTES:
            raise RestConnectorError("headers exceed the size limit")
        out[name] = val
    return out


class RestConnector:
    def __init__(self, allowed_hosts: set[str] | frozenset[str], *, transport: RestTransport, timeout_seconds: float = 10.0):
        hosts = frozenset(str(h).strip().lower().rstrip(".") for h in allowed_hosts if str(h).strip())
        if not hosts:
            raise RestConnectorError("at least one explicit host allowlist entry is required")
        if transport is None:
            raise RestConnectorError("an injected transport is required")
        self.allowed_hosts = hosts
        self.transport = transport
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))

    def request(self, request: RestRequest, *, approved: bool = False, resolve_dns: bool = False) -> RestResponse:
        method = request.method.upper().strip()
        if method not in SAFE_METHODS | WRITE_METHODS:
            raise RestConnectorError("HTTP method is not supported")
        if method in WRITE_METHODS and not approved:
            raise RestConnectorError("write requests require explicit human approval")
        body = request.body if isinstance(request.body, bytes) else bytes(request.body)
        if len(body) > MAX_REQUEST_BYTES:
            raise RestConnectorError("request body exceeds the size limit")
        url = validate_public_https_url(request.url, self.allowed_hosts, resolve_dns=resolve_dns)
        headers = validate_headers(request.headers)
        if method in WRITE_METHODS:
            key = request.idempotency_key or deterministic_idempotency_key(method, url, body)
            headers = {**headers, "Idempotency-Key": key}
        retries = 0
        redirects = 0
        while True:
            response = self.transport.request(method, url, headers=headers, body=body, timeout=self.timeout_seconds)
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise RestConnectorError("response exceeds the size limit")
            location = next((v for k, v in response.headers.items() if k.lower() == "location"), None)
            if response.status_code in {301, 302, 303, 307, 308} and location:
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise RestConnectorError("redirect limit exceeded")
                url = validate_public_https_url(urljoin(url, location), self.allowed_hosts, resolve_dns=resolve_dns)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and method in SAFE_METHODS and retries < MAX_RETRIES:
                retries += 1
                continue
            return response

    def safe_json(self, request: RestRequest, *, approved: bool = False, resolve_dns: bool = False) -> dict[str, Any]:
        response = self.request(request, approved=approved, resolve_dns=resolve_dns)
        result = response.safe_dict()
        content_type = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "")
        if "json" in content_type.lower():
            try:
                result["json"] = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["json_error"] = "invalid JSON response"
        return result
