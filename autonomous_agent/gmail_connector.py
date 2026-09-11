from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_QUERY_LENGTH = 500
MAX_RESULTS = 20
MAX_MESSAGE_BYTES = 256 * 1024
MAX_THREAD_MESSAGES = 25
MAX_ATTACHMENT_METADATA = 20
MAX_RETRIES = 2
MAX_TIMEOUT_SECONDS = 30
MAX_BODY_CHARS = 100_000

_SECRET = re.compile(r"(?i)(?:bearer\s+|api[_-]?key\s*[:=]\s*|access[_-]?token\s*[:=]\s*|refresh[_-]?token\s*[:=]\s*|password\s*[:=]\s*|secret\s*[:=\s*])[^\s,;]+")
_AUTH = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailError(ValueError):
    pass


class GmailTransport(Protocol):
    def request(self, method: str, url: str, *, params: Mapping[str, Any] | None = None, body: Mapping[str, Any] | None = None, timeout_seconds: int = 10) -> Mapping[str, Any]: ...


class CredentialResolver(Protocol):
    def resolve(self, credential_reference: str) -> str: ...


@dataclass(frozen=True)
class GmailOAuthConfig:
    credential_reference: str
    scopes: tuple[str, ...]

    def __post_init__(self):
        if not self.credential_reference or any(x in self.credential_reference.lower() for x in ("token", "password", "secret", "key=")):
            raise GmailError("OAuth configuration must contain a reference, not credential material")
        if not self.scopes:
            raise GmailError("at least one Gmail OAuth scope is required")


@dataclass(frozen=True)
class GmailEvidence:
    operation: str
    data: Mapping[str, Any]
    fingerprint: str

    def safe_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "data": self.data, "fingerprint": self.fingerprint}


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _redact(v) for k, v in value.items() if str(k).lower() not in {"access_token", "refresh_token", "authorization", "password", "client_secret"}}
    if isinstance(value, list):
        return [_redact(v) for v in value[:MAX_ATTACHMENT_METADATA]]
    if isinstance(value, str):
        value = _AUTH.sub(r"\1[REDACTED]", value)
        return _SECRET.sub("[REDACTED]", value)[:MAX_BODY_CHARS]
    return value


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(_redact(payload), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _bounded_text(value: Any, limit: int = MAX_BODY_CHARS) -> str:
    return str(value or "")[:limit]


def _safe_query(query: str) -> str:
    if not isinstance(query, str):
        raise GmailError("query must be a string")
    query = " ".join(query.split())
    if len(query) > MAX_QUERY_LENGTH:
        raise GmailError("Gmail search query exceeds the bounded length")
    if any(ord(ch) < 32 and ch not in "\t" for ch in query):
        raise GmailError("Gmail search query contains control characters")
    return query


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ch in value for ch in "\r\n"):
        raise GmailError(f"invalid {label}")
    return value.strip()


def _parse_mime(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise GmailError("message exceeds the bounded size")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts: list[dict[str, Any]] = []
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename:
            parts.append({"filename": _bounded_text(filename, 256), "content_type": part.get_content_type(), "size": len(part.get_payload(decode=True) or b"")})
            if len(parts) >= MAX_ATTACHMENT_METADATA:
                break
        else:
            try:
                content = part.get_content()
            except Exception:
                content = ""
            if part.get_content_type() == "text/plain":
                text_parts.append(_bounded_text(content))
            elif part.get_content_type() == "text/html":
                html_parts.append(_bounded_text(re.sub(r"<[^>]+>", " ", html.unescape(str(content)))))
    return {"subject": _bounded_text(message.get("subject", ""), 512), "from": _bounded_text(message.get("from", ""), 512), "to": _bounded_text(message.get("to", ""), 512), "date": _bounded_text(message.get("date", ""), 128), "text": _redact("\n".join(text_parts)), "html_text": _redact("\n".join(html_parts)), "attachments": parts}


class GmailConnector:
    """Official Gmail REST resource adapter with injected transport and no credential storage."""

    def __init__(self, transport: GmailTransport, *, credential_reference: str = "gmail:oauth:user", credential_resolver: CredentialResolver | None = None, timeout_seconds: int = 10):
        if not credential_reference or any(x in credential_reference.lower() for x in ("token", "password", "secret", "key=")):
            raise GmailError("credential reference is invalid")
        self.transport = transport
        self.credential_reference = credential_reference
        self.credential_resolver = credential_resolver
        self.timeout_seconds = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))

    def _request(self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Mapping[str, Any] | None = None, retries: int = MAX_RETRIES) -> Mapping[str, Any]:
        if not path.startswith(GMAIL_API_ROOT + "/"):
            raise GmailError("request escaped the official Gmail API root")
        attempts = max(1, min(int(retries) + 1, MAX_RETRIES + 1))
        last: Exception | None = None
        for _ in range(attempts):
            try:
                result = self.transport.request(method, path, params=params, body=body, timeout_seconds=self.timeout_seconds)
                if not isinstance(result, Mapping):
                    raise GmailError("Gmail transport returned a malformed response")
                return _redact(result)
            except Exception as exc:
                last = exc
        raise GmailError(f"Gmail request failed after bounded retries: {type(last).__name__}")

    def search(self, query: str, *, results: int = 10) -> GmailEvidence:
        query = _safe_query(query)
        results = max(1, min(int(results), MAX_RESULTS))
        payload = self._request("GET", f"{GMAIL_API_ROOT}/messages", params={"q": query, "maxResults": results})
        messages = payload.get("messages", []) if isinstance(payload.get("messages", []), list) else []
        messages = [{"id": _safe_id(str(x.get("id", "")), "message id"), "threadId": _safe_id(str(x.get("threadId", "")), "thread id")} for x in messages[:results] if isinstance(x, Mapping) and x.get("id")]
        data = {"messages": messages, "resultSizeEstimate": int(payload.get("resultSizeEstimate", 0) or 0)}
        return GmailEvidence("email.search", data, _fingerprint(data))

    def read(self, message_id: str) -> GmailEvidence:
        message_id = _safe_id(message_id, "message id")
        payload = self._request("GET", f"{GMAIL_API_ROOT}/messages/{quote(message_id, safe='')}", params={"format": "full"})
        raw = payload.get("raw")
        parsed = _parse_mime(base64.urlsafe_b64decode(str(raw) + "==")) if raw else {"subject": _bounded_text(payload.get("payload", {}).get("headers", []), 512), "snippet": _bounded_text(payload.get("snippet", ""))}
        data = {"id": message_id, "threadId": _bounded_text(payload.get("threadId", ""), 512), "labelIds": [str(x) for x in payload.get("labelIds", [])[:20]], "snippet": _bounded_text(payload.get("snippet", "")), "content": _redact(parsed)}
        return GmailEvidence("email.read", data, _fingerprint(data))

    def thread(self, thread_id: str) -> GmailEvidence:
        thread_id = _safe_id(thread_id, "thread id")
        payload = self._request("GET", f"{GMAIL_API_ROOT}/threads/{quote(thread_id, safe='')}", params={"format": "full"})
        messages = payload.get("messages", []) if isinstance(payload.get("messages", []), list) else []
        normalized = []
        for item in messages[:MAX_THREAD_MESSAGES]:
            if not isinstance(item, Mapping):
                continue
            normalized.append({"id": _bounded_text(item.get("id", ""), 512), "threadId": _bounded_text(item.get("threadId", thread_id), 512), "internalDate": _bounded_text(item.get("internalDate", ""), 32), "snippet": _bounded_text(item.get("snippet", ""))})
        normalized.sort(key=lambda x: (x["internalDate"], x["id"]))
        data = {"threadId": thread_id, "messages": normalized}
        return GmailEvidence("email.thread", data, _fingerprint(data))

    def draft(self, *, to: str, subject: str, body: str, thread_id: str | None = None) -> GmailEvidence:
        if not isinstance(to, str) or "\n" in to or "\r" in to or len(to) > 2048:
            raise GmailError("invalid recipient")
        if not isinstance(subject, str) or "\n" in subject or "\r" in subject or len(subject) > 998:
            raise GmailError("invalid subject")
        body = _bounded_text(body)
        message = {"to": to.strip(), "subject": subject, "body": body}
        if thread_id is not None:
            message["threadId"] = _safe_id(thread_id, "thread id")
        data = {"draft": _redact(self._request("POST", f"{GMAIL_API_ROOT}/drafts", body={"message": message})), "content_fingerprint": _fingerprint(message)}
        return GmailEvidence("email.draft", data, _fingerprint(data))

    def send(self, *, to: str, subject: str, body: str, idempotency_key: str, approved: bool, thread_id: str | None = None) -> GmailEvidence:
        if not approved:
            raise GmailError("email.send requires explicit human approval")
        key = _safe_id(idempotency_key, "idempotency key")
        content = {"to": to, "subject": subject, "body": _bounded_text(body), "threadId": thread_id or ""}
        digest = _fingerprint(content)
        if key != digest:
            raise GmailError("idempotency key does not match the message digest")
        message = {"to": to, "subject": subject, "body": _bounded_text(body), "idempotencyKey": key}
        if thread_id:
            message["threadId"] = _safe_id(thread_id, "thread id")
        data = _redact(self._request("POST", f"{GMAIL_API_ROOT}/messages/send", body={"message": message}, retries=0))
        evidence = {"recipient": to, "message_fingerprint": digest, "response": data}
        return GmailEvidence("email.send", evidence, _fingerprint(evidence))


def gmail_oauth_scopes(*, include_send: bool = False) -> tuple[str, ...]:
    scopes = [READ_SCOPE, COMPOSE_SCOPE]
    if include_send:
        scopes.append(SEND_SCOPE)
    return tuple(scopes)
