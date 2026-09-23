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
        try:
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
            raise RestConnectorError("request retry/redirect policy exhausted")
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
