from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BODY_BYTES = 8_192
MAX_RESPONSE_BYTES = 2_000_000
SESSION_COOKIE = "scout_session"
SESSION_MAX_AGE_SECONDS = 43_200
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SESSION_PATTERN = re.compile(r"^v1\.([0-9]{10,12})\.([A-Za-z0-9_-]{16,32})\.([a-f0-9]{64})$")
_HTML_CSP = (
    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)


def _session_signature(token: str, issued_at: str, nonce: str) -> str:
    """Authenticate an opaque browser session without placing its token in a cookie."""
    message = f"autonomous-ai-scout/session/v1:{issued_at}:{nonce}".encode("ascii")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def normalize_backend_origin(value: str) -> str:
    """Accept only an HTTPS origin, never an arbitrary path or URL."""
    parsed = urlsplit(str(value).strip())
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("SCOUT_API_ORIGIN must be an HTTPS origin without a path")
    return f"https://{parsed.netloc}"


def target_is_allowed(path: str, method: str) -> bool:
    """Allow only the Mission Control read/list/submit/approval API surface."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return False
    normalized = parsed.path
    if not normalized.startswith("/") or normalized.startswith("//"):
        return False
    if any(part in {".", ".."} for part in normalized.split("/")):
        return False
    if normalized == "/api/proxy":
        return False

    verb = method.upper()
    if verb == "GET":
        if normalized in {"/", "/login", "/health", "/api/missions", "/api/approvals"}:
            return True
        if normalized.startswith("/api/missions/"):
            parts = normalized.strip("/").split("/")
            return (
                len(parts) == 3 and bool(_RESOURCE_ID_PATTERN.fullmatch(parts[2]))
            ) or (
                len(parts) == 4 and bool(_RESOURCE_ID_PATTERN.fullmatch(parts[2]))
                and parts[3] in {"memory", "timeline"}
            )
        # Nested approval endpoints are mutations and are never GET routes.
        return False

    if verb != "POST":
        return False
    if normalized in {"/login", "/logout", "/api/missions"}:
        return True
    if normalized.startswith("/api/approvals/"):
        parts = normalized.strip("/").split("/")
        return (len(parts) == 4 and bool(_RESOURCE_ID_PATTERN.fullmatch(parts[2]))
                and parts[3] in {"approve", "reject"})
    if normalized.startswith("/api/missions/"):
        parts = normalized.strip("/").split("/")
        return (len(parts) == 4 and bool(_RESOURCE_ID_PATTERN.fullmatch(parts[2]))
                and parts[3] in {"cancel", "resume"})
    return False


def resolve_request_path(request_target: str, method: str) -> str:
    """Resolve direct service paths or a single legacy ?path= target safely."""
    parsed = urlsplit(request_target)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "path" in query:
        if parsed.path != "/api/proxy":
            return ""
        values = query["path"]
        if len(values) != 1:
            return ""
        candidate = values[0]
        return candidate if target_is_allowed(candidate, method) else ""
    if target_is_allowed(parsed.path, method):
        return parsed.path
    return ""


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'">
<title>Autonomous AI Scout · Sign in</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#edf2f7}
main{width:min(420px,calc(100% - 40px));padding:28px;background:#111827;border:1px solid #334155;border-radius:16px;box-sizing:border-box}
p{color:#a0aec0;line-height:1.5}label{display:block;margin:18px 0 8px}input{box-sizing:border-box;width:100%;padding:12px;border-radius:8px;border:1px solid #475569;background:#0b1020;color:#fff}
button{margin-top:14px;width:100%;padding:12px;border:0;border-radius:8px;background:#2563eb;color:white;font-weight:700;cursor:pointer}#message{min-height:24px;color:#fca5a5;margin-top:12px}
</style>
</head>
<body><main>
<h1>Autonomous AI Scout</h1>
<p>This page is only for the Autonomous AI Scout workspace. Enter the project-specific workspace token only; this is not a Google, GitHub, Microsoft, or other account sign-in.</p>
<p>The token is sent only to this site's own <code>/login</code> endpoint. Do not enter a password used on any other service.</p>
<p><a href="https://github.com/Pappu246/autonomous-ai-scout" rel="noopener noreferrer">View the source repository</a></p>
<form id="login">
<label for="token">Workspace access password</label>
<input id="token" name="token" type="password" required autocomplete="current-password" minlength="32">
<button type="submit">Sign in</button>
</form><div id="message" role="status" aria-live="polite"></div>
<script>
document.getElementById('login').addEventListener('submit', async event => {
  event.preventDefault();
  const message = document.getElementById('message');
  message.textContent = 'Checking access…';
  try {
    const response = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: document.getElementById('token').value})
    });
    if (!response.ok) throw new Error('Access denied. Check the password and try again.');
    location.replace('/');
  } catch (error) { message.textContent = error.message; }
});
</script>
</main></body></html>"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Never forward the backend bearer token to a redirect target.
        return None


class ScoutVercelProxyHandler(BaseHTTPRequestHandler):
    """Server-side Vercel gateway; browser sessions never receive the backend token."""

    server_version = "AutonomousAIScoutGateway/1.0"

    def _reply(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if content_type.lower().startswith("text/html"):
            self.send_header("Content-Security-Policy", _HTML_CSP)
        self.send_header("Content-Length", str(len(body)))
        protected = {
            "content-length", "content-type", "connection", "transfer-encoding",
            "cache-control", "content-security-policy", "x-content-type-options",
            "referrer-policy", "x-frame-options",
        }
        for key, value in (extra_headers or {}).items():
            if key.lower() not in protected:
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, *, extra_headers: dict[str, str] | None = None) -> None:
        self._reply(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            extra_headers=extra_headers,
        )

    def _configured_token(self, name: str) -> str:
        value = os.getenv(name, "").strip()
        return value if _TOKEN_PATTERN.fullmatch(value) else ""

    def _original_path(self) -> str:
        return resolve_request_path(self.path, self.command)

    def _vercel_sso_enforced(self) -> bool:
        # Vercel Deployment Protection authenticates every request for Preview
        # deployments. Never show a second public credential-collection form
        # on those URLs; use the platform SSO gate instead.
        return os.getenv("VERCEL_ENV", "").strip().lower() == "preview"

    def _has_session(self) -> bool:
        expected = self._configured_token("SCOUT_WEB_ACCESS_TOKEN")
        if not expected:
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        cookie = cookies.get(SESSION_COOKIE)
        match = _SESSION_PATTERN.fullmatch(cookie.value) if cookie else None
        if not match:
            return False
        issued_at, nonce, signature = match.groups()
        now = int(time.time())
        age = now - int(issued_at)
        if age < -60 or age > SESSION_MAX_AGE_SECONDS:
            return False
        return hmac.compare_digest(signature, _session_signature(expected, issued_at, nonce))

    def _login_page(self) -> None:
        if not self._configured_token("SCOUT_WEB_ACCESS_TOKEN"):
            self._json(503, {"error": "SCOUT_WEB_ACCESS_TOKEN is not configured for this deployment"})
            return
        self._reply(200, _LOGIN_PAGE.encode("utf-8"), content_type="text/html; charset=utf-8")

    def _request_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        return body

    def do_GET(self) -> None:
        path = self._original_path()
        if not target_is_allowed(path, "GET"):
            self._json(404, {"error": "route not found"})
            return
        if self._vercel_sso_enforced():
            # The Vercel edge authenticates Preview requests before routing.
            # /login must not render a password form on a flagged preview host.
            if path == "/login":
                self._reply(
                    303,
                    b"",
                    extra_headers={"Location": "/", "Content-Length": "0"},
                )
                return
            self._proxy(path, "GET")
            return
        if path in {"/", "/login"}:
            if not self._configured_token("SCOUT_WEB_ACCESS_TOKEN"):
                self._json(503, {"error": "SCOUT_WEB_ACCESS_TOKEN is not configured for this deployment"})
                return
            if not self._has_session():
                self._login_page()
                return
            if path == "/login":
                path = "/"
        elif path != "/health" and not self._has_session():
            self._json(401, {"error": "sign in required"})
            return
        self._proxy(path, "GET")

    def do_POST(self) -> None:
        path = self._original_path()
        if path == "/login":
            if self._vercel_sso_enforced():
                self._json(
                    410,
                    {"error": "Preview access uses Vercel Authentication; this endpoint does not accept passwords."},
                )
                return
            expected = self._configured_token("SCOUT_WEB_ACCESS_TOKEN")
            if not expected:
                self._json(503, {"error": "SCOUT_WEB_ACCESS_TOKEN is not configured for this deployment"})
                return
            try:
                payload = json.loads(self._request_body().decode("utf-8"))
                provided = payload.get("token", "") if isinstance(payload, dict) else ""
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._json(400, {"error": "invalid login request"})
                return
            if not isinstance(provided, str) or not hmac.compare_digest(provided, expected):
                self._json(401, {"error": "invalid access password"})
                return
            issued_at = str(int(time.time()))
            nonce = secrets.token_urlsafe(12)
            signature = _session_signature(expected, issued_at, nonce)
            session = f"v1.{issued_at}.{nonce}.{signature}"
            cookie = (
                f"{SESSION_COOKIE}={session}; Path=/; Max-Age={SESSION_MAX_AGE_SECONDS}; "
                "Secure; HttpOnly; SameSite=Strict"
            )
            self._json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            return
        if path == "/logout":
            cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"
            self._json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            return
        if not target_is_allowed(path, "POST"):
            self._json(404, {"error": "route not found"})
            return
        if not self._vercel_sso_enforced() and not self._has_session():
            self._json(401, {"error": "sign in required"})
            return
        self._proxy(path, "POST")

    def _proxy(self, path: str, method: str) -> None:
        try:
            origin = normalize_backend_origin(os.getenv("SCOUT_API_ORIGIN", ""))
        except ValueError:
            self._json(503, {"error": "SCOUT_API_ORIGIN must be configured as the Azure HTTPS origin"})
            return
        server_token = self._configured_token("SCOUT_SERVER_TOKEN")
        if not server_token:
            self._json(503, {"error": "SCOUT_SERVER_TOKEN is missing or too short"})
            return

        try:
            body = self._request_body() if method == "POST" else None
        except ValueError as exc:
            self._json(413, {"error": str(exc)})
            return

        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "X-Autonomous-Scout-Token": server_token,
        }
        content_type = self.headers.get("Content-Type", "")
        if body is not None and content_type:
            headers["Content-Type"] = content_type
        try:
            request = Request(origin + path, data=body, headers=headers, method=method)
            opener = build_opener(_NoRedirect())
            try:
                upstream = opener.open(request, timeout=30)
            except HTTPError as exc:
                if 300 <= exc.code < 400:
                    exc.close()
                    self._json(502, {"error": "backend redirects are not allowed"})
                    return
                upstream = exc
            try:
                payload = upstream.read(MAX_RESPONSE_BYTES + 1)
                status = int(getattr(upstream, "status", getattr(upstream, "code", 502)))
                upstream_type = upstream.headers.get("Content-Type", "application/octet-stream")
            finally:
                upstream.close()
        except (URLError, TimeoutError, OSError) as exc:
            self._json(502, {"error": f"backend request failed: {type(exc).__name__}"})
            return

        if len(payload) > MAX_RESPONSE_BYTES:
            self._json(502, {"error": "backend response exceeded the gateway size limit"})
            return

        if path == "/health" and "json" in upstream_type.lower():
            try:
                health = json.loads(payload.decode("utf-8"))
                if isinstance(health, dict):
                    health.pop("workspace_root", None)
                    payload = json.dumps(health, sort_keys=True).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        if path == "/" and "text/html" in upstream_type.lower() and not self._vercel_sso_enforced():
            page = payload.decode("utf-8", errors="replace")
            logout = """<aside style="position:fixed;top:12px;right:12px;z-index:9999"><button id="scout-logout" type="button" style="background:#334155;color:white;border:0;border-radius:8px;padding:9px 12px;cursor:pointer">Sign out</button></aside><script>document.getElementById('scout-logout')?.addEventListener('click',async()=>{await fetch('/logout',{method:'POST'});location.replace('/');});</script>"""
            page = page.replace("</body>", logout + "</body>")
            payload = page.encode("utf-8")

        # Never inherit a cacheable upstream policy for private mission data.
        self._reply(status, payload, content_type=upstream_type)

    def log_message(self, format: str, *args: object) -> None:
        # Never log credentials, query strings, task text, or cookies.
        return
