"""Platform-independent security policy and bounds for the bounded browser agent.

This module is the single source of truth for *what is allowed to leave the
sandbox*: which URLs may be navigated to, which redirects are acceptable, which
hosts are forbidden regardless of the allowlist, how downloads are confined and
sanitized, and how selectors and typed text are bounded.

Every function fails closed: on any doubt it raises
:class:`~autonomous_agent.browser.models.BrowserSecurityError` rather than
returning a permissive answer.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from .models import (
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    MAX_SELECTOR_LENGTH,
    MAX_TYPED_LENGTH,
    MAX_URL_LENGTH,
    BrowserSecurityError,
    is_credential_field,
    looks_like_secret,
    redact_secret,
)


ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})

# Schemes that are rejected outright. Navigation to any of these is a security
# boundary violation, never a soft failure.
BLOCKED_SCHEMES: frozenset[str] = frozenset(
    {
        "file",
        "javascript",
        "data",
        "blob",
        "about",
        "ftp",
        "ftps",
        "ws",
        "wss",
        "gopher",
        "mailto",
        "tel",
        "chrome",
        "chromium",
        "moz-extension",
        "resource",
        "view-source",
        "jar",
    }
)

# Only the default HTTPS port is permitted; every other port is "unsafe".
ALLOWED_PORTS: frozenset[int | None] = frozenset({None, 443})

# Hostnames that always resolve to the local machine, a metadata service, or a
# link-local/mDNS namespace. Blocked regardless of the operator allowlist.
BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.rfc.ncse",
    }
)
BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (".localhost", ".internal", ".local", ".home.arpa")

# The cloud instance metadata endpoint (also link-local, but named explicitly).
METADATA_ADDRESSES: frozenset[str] = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


# --------------------------------------------------------------------------
# Host validation
# --------------------------------------------------------------------------
def _is_blocked_ip(literal: str) -> bool:
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        return False
    if literal in METADATA_ADDRESSES:
        return True
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_blocked_host(host: str | None) -> bool:
    """True when a host is loopback, private, link-local, or a metadata service.

    This is independent of the allowlist: even a host the operator accidentally
    allowlisted is refused if it points at the local machine or a metadata
    endpoint.
    """
    if not host:
        return True
    normalized = host.strip().lower().rstrip(".")
    if not normalized:
        return True
    if normalized in BLOCKED_HOSTNAMES:
        return True
    for suffix in BLOCKED_HOST_SUFFIXES:
        if normalized.endswith(suffix):
            return True
    if _is_blocked_ip(normalized):
        return True
    return False


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def validate_host_allowlist(allowed_hosts) -> frozenset[str]:
    """Normalize and validate an operator-provided host allowlist.

    An empty allowlist is a security error: navigation is never "allow all".
    Blocked hosts (loopback/metadata/private) are refused even if listed.
    """
    if allowed_hosts is None:
        raise BrowserSecurityError("an explicit host allowlist is required")
    if isinstance(allowed_hosts, str):
        candidates = [allowed_hosts]
    else:
        candidates = list(allowed_hosts)
    normalized: set[str] = set()
    for entry in candidates:
        if not isinstance(entry, str):
            raise BrowserSecurityError("allowlist entries must be hostname strings")
        host = normalize_host(entry)
        if not host:
            continue
        if is_blocked_host(host):
            raise BrowserSecurityError(f"allowlist host is forbidden: {host}")
        normalized.add(host)
    if not normalized:
        raise BrowserSecurityError("host allowlist cannot be empty")
    return frozenset(normalized)


def _validate_url_core(url: str, allowed_hosts: frozenset[str], *, kind: str) -> tuple[str, str]:
    """Shared, fail-closed URL validation for navigation and downloads."""
    if not isinstance(url, str):
        raise BrowserSecurityError(f"{kind} URL must be a string")
    if not url or len(url) > MAX_URL_LENGTH:
        raise BrowserSecurityError(f"{kind} URL is empty or exceeds the length bound")
    # Reject embedded credentials before parsing can hide them.
    if "@" in url.split("://", 1)[-1].split("/", 1)[0]:
        raise BrowserSecurityError(f"{kind} URL must not contain embedded credentials")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES:
        raise BrowserSecurityError(f"{kind} scheme is forbidden: {scheme}")
    if scheme not in ALLOWED_SCHEMES:
        raise BrowserSecurityError(f"{kind} must use HTTPS (got scheme {scheme or 'none'})")
    if parsed.username or parsed.password:
        raise BrowserSecurityError(f"{kind} URL must not contain embedded credentials")
    host = parsed.hostname
    if not host:
        raise BrowserSecurityError(f"{kind} URL has no hostname")
    normalized = normalize_host(host)
    if is_blocked_host(normalized):
        raise BrowserSecurityError(f"{kind} host is forbidden: {normalized}")
    if normalized not in allowed_hosts:
        raise BrowserSecurityError(f"{kind} host is not in the allowlist: {normalized}")
    if parsed.port not in ALLOWED_PORTS:
        raise BrowserSecurityError(f"{kind} must use the default HTTPS port (443)")
    return normalized, scheme


def validate_navigation_url(url: str, allowed_hosts: frozenset[str]) -> str:
    """Validate a top-level navigation target. Returns the normalized host."""
    host, _ = _validate_url_core(url, allowed_hosts, kind="navigation")
    return host


def validate_redirect(
    from_url: str,
    to_url: str,
    allowed_hosts: frozenset[str],
    *,
    depth: int,
) -> str:
    """Validate one redirect hop.

    Redirects are re-validated against the same navigation policy (every hop
    must still be HTTPS, allowlisted, non-blocked) and the redirect depth is
    bounded.
    """
    if depth < 0:
        raise BrowserSecurityError("redirect depth cannot be negative")
    if depth >= MAX_REDIRECTS:
        raise BrowserSecurityError(
            f"redirect depth exceeds the bound of {MAX_REDIRECTS}"
        )
    # Validate the source too, so a chain that started off-allowlist cannot be
    # laundered through a redirect.
    validate_navigation_url(from_url, allowed_hosts)
    host, _ = _validate_url_core(to_url, allowed_hosts, kind="redirect")
    return host


def validate_download_url(url: str, allowed_hosts: frozenset[str]) -> str:
    """Validate a download URL against the same navigation policy."""
    host, _ = _validate_url_core(url, allowed_hosts, kind="download")
    return host


# --------------------------------------------------------------------------
# Response / evidence sizing
# --------------------------------------------------------------------------
def bound_response_size(size_bytes: int) -> int:
    """Clamp/validate a response size against the bounded evidence limit."""
    try:
        value = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise BrowserSecurityError("response size must be an integer") from exc
    if value < 0:
        raise BrowserSecurityError("response size cannot be negative")
    if value > MAX_RESPONSE_BYTES:
        raise BrowserSecurityError(
            f"response size exceeds the bound: {value} > {MAX_RESPONSE_BYTES}"
        )
    return value


def bound_download_size(size_bytes: int) -> int:
    """Validate a download size against the bounded download limit."""
    try:
        value = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise BrowserSecurityError("download size must be an integer") from exc
    if value < 0:
        raise BrowserSecurityError("download size cannot be negative")
    if value > MAX_DOWNLOAD_BYTES:
        raise BrowserSecurityError(
            f"download size exceeds the bound: {value} > {MAX_DOWNLOAD_BYTES}"
        )
    return value


# --------------------------------------------------------------------------
# Download path confinement and filename sanitization
# --------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Return a safe, single-segment filename.

    Strips directory separators, parent references, control characters and any
    character outside a conservative safe set. Falls back to a fixed name when
    nothing usable remains.
    """
    if not isinstance(name, str):
        raise BrowserSecurityError("filename must be a string")
    candidate = name.strip().replace("\\", "/")
    # Keep only the final path segment; drop any traversal attempt.
    candidate = candidate.split("/")[-1]
    candidate = candidate.replace("\x00", "")
    candidate = _SAFE_FILENAME.sub("_", candidate)
    candidate = candidate.strip("._")
    if candidate in {"", ".", ".."}:
        return "download.bin"
    if len(candidate) > 128:
        root, dot, ext = candidate.rpartition(".")
        if dot and len(ext) <= 16:
            candidate = root[: 128 - len(ext) - 1] + "." + ext
        else:
            candidate = candidate[:128]
    return candidate


def confine_download_path(workspace_root: Path | str, filename: str) -> tuple[Path, str]:
    """Resolve a download destination strictly inside the workspace root.

    Returns ``(absolute_path, relative_path)``. Raises if the resolved path
    would escape the workspace root (path traversal defense) even after
    sanitization.
    """
    root = Path(workspace_root).resolve()
    safe_name = sanitize_filename(filename)
    # PurePosixPath guarantees no backslash/segment tricks survive.
    relative = PurePosixPath(safe_name)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise BrowserSecurityError("download filename resolves outside the workspace")
    destination = (root / safe_name).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise BrowserSecurityError("download path escapes the workspace root") from exc
    return destination, safe_name


def confine_workspace_path(workspace_root: Path | str, relative_path: str) -> tuple[Path, str]:
    """Resolve a *read* path strictly inside the workspace root.

    Unlike downloads this permits nested relative paths (e.g. ``downloads/a``),
    but still rejects absolute paths, parent traversal and symlink escapes.
    Returns ``(absolute_path, normalized_relative_path)``.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise BrowserSecurityError("a workspace-relative path is required")
    root = Path(workspace_root).resolve()
    candidate = PurePosixPath(relative_path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise BrowserSecurityError("path traversal is not permitted")
    destination = (root / candidate).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise BrowserSecurityError("path escapes the workspace root") from exc
    return destination, str(candidate)


# --------------------------------------------------------------------------
# Selector / typed text bounds
# --------------------------------------------------------------------------
def validate_selector(selector: str) -> str:
    """Validate a stable, bounded selector string. No coordinate fallback exists."""
    if not isinstance(selector, str):
        raise BrowserSecurityError("selector must be a string")
    cleaned = selector.strip()
    if not cleaned:
        raise BrowserSecurityError("selector cannot be empty")
    if len(cleaned) > MAX_SELECTOR_LENGTH:
        raise BrowserSecurityError(
            f"selector exceeds the length bound: {len(cleaned)} > {MAX_SELECTOR_LENGTH}"
        )
    if "\x00" in cleaned:
        raise BrowserSecurityError("selector cannot contain null bytes")
    return cleaned


def validate_typed_text(text: str) -> str:
    """Validate text to type into a non-credential field.

    Refuses credential material outright so a secret can never be typed into a
    page (and thus never enters page/audit context), and bounds the length.
    """
    if not isinstance(text, str):
        raise BrowserSecurityError("typed text must be a string")
    if len(text) > MAX_TYPED_LENGTH:
        raise BrowserSecurityError(
            f"typed text exceeds the length bound: {len(text)} > {MAX_TYPED_LENGTH}"
        )
    if looks_like_secret(text):
        raise BrowserSecurityError("credential material must never be typed into a page")
    return text


def assert_not_credential_field(element) -> None:
    """Fail closed if an element is a credential/secret field."""
    if element is None:
        raise BrowserSecurityError("target element is required")
    if is_credential_field(
        tag=getattr(element, "tag", ""),
        input_type=getattr(element, "input_type", ""),
        name=getattr(element, "accessible_name", ""),
        aria_label=getattr(element, "accessible_name", ""),
    ):
        raise BrowserSecurityError("interaction with a credential field is blocked")


__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "BLOCKED_HOSTNAMES",
    "BLOCKED_HOST_SUFFIXES",
    "BLOCKED_SCHEMES",
    "METADATA_ADDRESSES",
    "assert_not_credential_field",
    "bound_download_size",
    "bound_response_size",
    "confine_download_path",
    "confine_workspace_path",
    "is_blocked_host",
    "normalize_host",
    "sanitize_filename",
    "validate_download_url",
    "validate_host_allowlist",
    "validate_navigation_url",
    "validate_redirect",
    "validate_selector",
    "validate_typed_text",
    "redact_secret",
]
