"""Advanced bounded browser agent domain (Phase 3).

A stateful, bounded browser agent built strictly on top of the existing
authorization, sandbox, audit, consequence-policy, prompt-injection and
checkpoint/resume infrastructure. It introduces no second runtime and no
arbitrary code execution.

Guarantees enforced across this package:
- HTTPS-only navigation with an explicit host allowlist.
- Every redirect re-validated; bounded redirect depth.
- Dangerous URI schemes (file:, javascript:, data:, blob:, about:, ...) rejected.
- Loopback, private, link-local and cloud-metadata addresses blocked.
- Embedded credentials and unsafe ports rejected.
- Semantic target resolution only -- no blind coordinate fallback; stale or
  foreign targets fail closed.
- Credential fields and credential material are blocked and redacted.
- Downloads confined to the workspace root with sanitized filenames and size caps.
- Replay on resume never blindly repeats a completed mutation.
- Input dispatch alone is never VERIFIED; observable post-conditions are required.
- Live-browser environments that cannot be driven safely fail closed.

This milestone (M1) exposes only the models and the security policy. Later
milestones add the backend, session, target resolver, connector, observer,
replay protection and bounded workflow.
"""

from __future__ import annotations

from .models import (
    ActionBudget,
    BackendUnavailableError,
    BrowserError,
    BrowserReplayError,
    BrowserSecurityError,
    DownloadError,
    DownloadRecord,
    ElementTarget,
    NavigationError,
    NavigationRecord,
    PageElement,
    PageObservation,
    SessionError,
    SessionSnapshot,
    SessionState,
    TargetResolutionError,
    consequential_signal,
    is_credential_field,
    looks_like_secret,
    redact_secret,
)
from .policy import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    BLOCKED_HOSTNAMES,
    BLOCKED_HOST_SUFFIXES,
    BLOCKED_SCHEMES,
    METADATA_ADDRESSES,
    assert_not_credential_field,
    bound_download_size,
    bound_response_size,
    confine_download_path,
    is_blocked_host,
    normalize_host,
    sanitize_filename,
    validate_download_url,
    validate_host_allowlist,
    validate_navigation_url,
    validate_redirect,
    validate_selector,
    validate_typed_text,
)

__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "BLOCKED_HOSTNAMES",
    "BLOCKED_HOST_SUFFIXES",
    "BLOCKED_SCHEMES",
    "METADATA_ADDRESSES",
    "ActionBudget",
    "BackendUnavailableError",
    "BrowserError",
    "BrowserReplayError",
    "BrowserSecurityError",
    "DownloadError",
    "DownloadRecord",
    "ElementTarget",
    "NavigationError",
    "NavigationRecord",
    "PageElement",
    "PageObservation",
    "SessionError",
    "SessionSnapshot",
    "SessionState",
    "TargetResolutionError",
    "assert_not_credential_field",
    "bound_download_size",
    "bound_response_size",
    "confine_download_path",
    "consequential_signal",
    "is_blocked_host",
    "is_credential_field",
    "looks_like_secret",
    "normalize_host",
    "redact_secret",
    "sanitize_filename",
    "validate_download_url",
    "validate_host_allowlist",
    "validate_navigation_url",
    "validate_redirect",
    "validate_selector",
    "validate_typed_text",
]
