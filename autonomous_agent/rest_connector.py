from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from .external_side_effects import ExternalSideEffectStore, SideEffectError, canonical_request_digest
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
SECRET_RE = re.compile(
    r"(?i)(bearer\s+|api[_-]?key\s*=\s*|password\s*=\s*|secret\s*=\s*|token\s*=\s*)[^\s,;&]+"
)
_CREDENTIAL_REF_FORBIDDEN = ("token", "password", "secret", "key=")
_SENSITIVE_JSON_KEYS = frozenset({
    "authorization", "cookie", "set-cookie", "api_key", "access_token",
    "refresh_token", "client_secret", "password", "secret",
})


class RestConnectorError(ValueError):
    pass


class RestTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> "RestResponse": ...


@dataclass(frozen=True)
class RestResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def safe_dict(self) -> dict[str, Any]:
        return {
            "status_code": int(self.status_code),
            "headers": _redact_headers(self.headers),
            "body": _redact_text(self.body[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")),
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
    return {
        str(key): "[REDACTED]"
        if str(key).lower() in BLOCKED_HEADERS
        else _redact_text(str(value))
        for key, value in headers.items()
    }


def _redact_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if str(key).lower() in _SENSITIVE_JSON_KEYS
            else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value[:200]]
    if isinstance(value, str):
        return _redact_text(value)[:MAX_RESPONSE_BYTES]
    return value


def deterministic_idempotency_key(method: str, url: str, body: bytes) -> str:
    return hashlib.sha256(
        method.upper().encode() + b"\0" + url.encode() + b"\0" + body
    ).hexdigest()


def _resolve_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RestConnectorError("host DNS resolution failed") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise RestConnectorError("host DNS resolution returned no addresses")
        return sorted(set(addresses), key=str)


def _publicly_routable(address: ipaddress._BaseAddress) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_public_https_url(
    url: str,
    allowed_hosts: frozenset[str],
    *,
    resolve_dns: bool = True,
) -> str:
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
    if resolve_dns and not all(_publicly_routable(address) for address in _resolve_host_ips(host)):
        raise RestConnectorError("resolved host address is not publicly routable")
    return url


def validate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping) or len(headers) > MAX_HEADERS:
        raise RestConnectorError("headers exceed the allowed count")
    out = {}
    total = 0
    for key, value in headers.items():
        name = str(key).strip()
        val = str(value)
        if not name or any(ord(ch) < 32 for ch in name + val):
            raise RestConnectorError("invalid header characters")
        if name.lower() in BLOCKED_HEADERS:
            raise RestConnectorError("credential-bearing headers must use credential references")
        total += len(name.encode()) + len(val.encode())
        if total > MAX_HEADER_BYTES:
            raise RestConnectorError("headers exceed the size limit")
        out[name] = val
    return out


def _validate_credential_ref(value: str | None) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or any(marker in value.lower() for marker in _CREDENTIAL_REF_FORBIDDEN)
    ):
        raise RestConnectorError("credential_ref must be a metadata reference, not credential material")


class RestConnector:
    def __init__(
        self,
        allowed_hosts: set[str] | frozenset[str],
        *,
        transport: RestTransport,
        timeout_seconds: float = 10.0,
        side_effect_store: ExternalSideEffectStore | None = None,
    ):
        hosts = frozenset(
            str(host).strip().lower().rstrip(".")
            for host in allowed_hosts
            if str(host).strip()
        )
        if not hosts:
            raise RestConnectorError("at least one explicit host allowlist entry is required")
        if not callable(getattr(transport, "request", None)):
            raise RestConnectorError("REST transport must expose a request method")
        self.allowed_hosts = hosts
        self.transport = transport
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))
        self.side_effect_store = side_effect_store

    def request(
        self,
        request: RestRequest,
        *,
        approved: bool = False,
        resolve_dns: bool = True,
    ) -> RestResponse:
        if not isinstance(request, RestRequest):
            raise RestConnectorError("request must be a RestRequest")
        method = request.method.upper().strip()
        if method not in SAFE_METHODS | WRITE_METHODS:
            raise RestConnectorError("HTTP method is not supported")
        if method in WRITE_METHODS and not approved:
            raise RestConnectorError("write requests require explicit human approval")
        _validate_credential_ref(request.credential_ref)
        body = request.body if isinstance(request.body, bytes) else bytes(request.body)
        if len(body) > MAX_REQUEST_BYTES:
            raise RestConnectorError("request body exceeds the size limit")
        url = validate_public_https_url(request.url, self.allowed_hosts, resolve_dns=resolve_dns)
        headers = validate_headers(request.headers)
        side_effect_key = None
        if method in WRITE_METHODS:
            key = request.idempotency_key or deterministic_idempotency_key(method, url, body)
            headers = {**headers, "Idempotency-Key": key}
            side_effect_key = key
            if self.side_effect_store is not None:
                digest = canonical_request_digest("rest." + method, {"url": url, "body": body})
                try:
                    decision = self.side_effect_store.claim(
                        key=key,
                        operation="rest." + method,
                        request_digest=digest,
                    )
                except SideEffectError as exc:
                    raise RestConnectorError(str(exc)) from exc
                if not decision.allowed:
                    raise RestConnectorError(decision.reason)

        attempts = 1 + (MAX_RETRIES if method in SAFE_METHODS else 0)
        retry_attempt = 0
        redirect_count = 0
        while retry_attempt < attempts:
            response = self.transport.request(
                method,
                url,
                headers=headers,
                body=body,
                timeout=self.timeout_seconds,
            )
            if not isinstance(response, RestResponse):
                raise RestConnectorError("REST transport returned an invalid response")
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise RestConnectorError("response exceeds the size limit")
            location = next(
                (value for key, value in response.headers.items() if str(key).lower() == "location"),
                None,
            )
            if response.status_code in {301, 302, 303, 307, 308} and location:
                redirect_count += 1
                if redirect_count > MAX_REDIRECTS:
                    raise RestConnectorError("redirect limit exceeded")
                url = validate_public_https_url(
                    urljoin(url, str(location)),
                    self.allowed_hosts,
                    resolve_dns=resolve_dns,
                )
                retry_attempt += 1
                continue
            if (
                response.status_code in {429, 500, 502, 503, 504}
                and method in SAFE_METHODS
                and retry_attempt + 1 < attempts
            ):
                retry_attempt += 1
                continue
            if side_effect_key is not None and self.side_effect_store is not None:
                if 200 <= response.status_code < 400:
                    self.side_effect_store.mark_executed(
                        side_effect_key,
                        result_digest=hashlib.sha256(response.body).hexdigest(),
                    )
                else:
                    self.side_effect_store.mark_failed(
                        side_effect_key,
                        reason=f"provider returned HTTP {response.status_code}",
                    )
            return response
        except Exception as exc:
            if side_effect_key is not None and self.side_effect_store is not None:
                try:
                    self.side_effect_store.mark_unknown(
                        side_effect_key,
                        reason=f"{type(exc).__name__}: external REST outcome is unresolved",
                    )
                except SideEffectError:
                    pass
            raise
        raise RestConnectorError("request retry/redirect policy exhausted")

    def safe_json(
        self,
        request: RestRequest,
        *,
        approved: bool = False,
        resolve_dns: bool = True,
    ) -> dict[str, Any]:
        response = self.request(request, approved=approved, resolve_dns=resolve_dns)
        result = response.safe_dict()
        content_type = next(
            (value for key, value in response.headers.items() if str(key).lower() == "content-type"),
            "",
        )
        if "json" in str(content_type).lower():
            try:
                result["json"] = _redact_json(json.loads(response.body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["json_error"] = "invalid JSON response"
        return result
