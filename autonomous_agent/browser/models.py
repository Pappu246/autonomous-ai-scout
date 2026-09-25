"""Core models, exceptions, bounds, and redaction for the bounded browser agent.

Nothing in this module talks to a real browser. It defines the safe, bounded
data shapes and the fail-closed exceptions used across the browser domain so
that navigation, interaction, download and workflow layers all speak one
vocabulary and all refuse unsafe input the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# --------------------------------------------------------------------------
# Exceptions -- every one of these is a fail-closed signal, never a crash the
# caller is expected to recover from by loosening a boundary.
# --------------------------------------------------------------------------
class BrowserError(Exception):
    """Base exception for the browser domain."""


class BrowserSecurityError(BrowserError):
    """Raised when an action would violate a navigation/download/credential boundary."""


class NavigationError(BrowserError):
    """Raised when a navigation cannot be performed safely or at all."""


class TargetResolutionError(BrowserError):
    """Raised when a semantic target cannot be resolved on the current page.

    Stale or foreign targets fail closed rather than falling back to a blind
    coordinate click.
    """


class DownloadError(BrowserError):
    """Raised when a download violates size, host, path or safety controls."""


class SessionError(BrowserError):
    """Raised when an operation requires an open, valid browser session."""


class BrowserReplayError(BrowserError):
    """Raised when a resume would blindly repeat a completed mutating action."""


class BackendUnavailableError(BrowserError):
    """Raised when no real browser backend is available in this environment.

    Live-browser environments that cannot be driven safely fail closed instead
    of pretending success.
    """


# --------------------------------------------------------------------------
# Bounds -- every one of these is an upper bound. They are deliberately small.
# --------------------------------------------------------------------------
MAX_URL_LENGTH = 2048
MAX_TEXT_LENGTH = 16_384
MAX_ELEMENTS = 200
MAX_LINKS = 200
MAX_SELECTOR_LENGTH = 512
MAX_TYPED_LENGTH = 4096
MAX_RESPONSE_BYTES = 2_000_000          # bounded page/evidence size
MAX_DOWNLOAD_BYTES = 25_000_000         # bounded download size
MAX_REDIRECTS = 5                       # bounded redirect depth
MAX_NAVIGATIONS = 40                    # bounded navigation history per session
MAX_ACTION_BUDGET = 60                  # bounded total actions per session
MAX_WORKFLOW_STEPS = 25                 # bounded multi-step workflow


# --------------------------------------------------------------------------
# Credential material -- reused, not re-invented. Kept local so the browser
# package has no import cycle with the digital provider.
# --------------------------------------------------------------------------
_CREDENTIAL_MATERIAL = re.compile(
    r"(?i)((?:\bapi[_-]?key\b|\baccess[_-]?token\b|\brefresh[_-]?token\b|\btoken\b|"
    r"\bauthorization\b|\bpassword\b|\bpasswd\b|\bsecret\b|\bprivate[_-]?key\b|"
    r"\bclient[_-]?secret\b|\bcredentials?\b)\s*[:=]\s*)\S+"
)
_BEARER_MATERIAL = re.compile(r"(?i)(bearer\s+)\S+")
REDACTED = "[REDACTED]"

# Compact tokens (letters+digits only) that mark a form control as a credential
# field. Matched against a compacted view of the control's autocomplete/name/aria
# so "credit card number", "api_key", "current-password" all normalize cleanly.
_CREDENTIAL_FIELD_TOKENS = (
    "password",
    "passwd",
    "currentpassword",
    "newpassword",
    "apikey",
    "token",
    "secret",
    "privatekey",
    "clientsecret",
    "creditcard",
    "cardnumber",
    "cvv",
    "ssn",
)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def looks_like_secret(text: str) -> bool:
    """True when text contains credential material or a bearer token."""
    if not isinstance(text, str) or not text:
        return False
    return bool(_CREDENTIAL_MATERIAL.search(text) or _BEARER_MATERIAL.search(text))


def redact_secret(text: str) -> str:
    """Strip credential material from text so it never reaches model/audit context."""
    if not isinstance(text, str):
        return text
    stripped = _BEARER_MATERIAL.sub(r"\1" + REDACTED, text)
    return _CREDENTIAL_MATERIAL.sub(r"\1" + REDACTED, stripped)


def is_credential_field(
    *,
    tag: str = "",
    input_type: str = "",
    name: str = "",
    autocomplete: str = "",
    aria_label: str = "",
) -> bool:
    """True when a form control is a credential/secret field and must be blocked.

    Password inputs, autocomplete credential hints, and any control whose
    name/aria hints at a secret are all treated as off-limits for interaction.
    """
    if str(input_type).strip().lower() in {"password"}:
        return True
    haystacks = (
        _compact(autocomplete),
        _compact(name),
        _compact(aria_label),
    )
    for token in _CREDENTIAL_FIELD_TOKENS:
        for value in haystacks:
            if value and token in value:
                return True
    return False


# --------------------------------------------------------------------------
# Consequential action detection -- high-impact mutations stay approval-gated
# even though the registry already marks the tools as requiring approval. This
# is a second, content-based gate keyed on the human-visible label.
# --------------------------------------------------------------------------
_CONSEQUENTIAL_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bpay\b|\bpayment\b|\bcheckout\b|\bplace order\b|\bbuy now\b|\bpurchase\b",
        r"\bdelete\b|\bdelete account\b|\bremove account\b|\bclose account\b|\bdeactivate\b",
        r"\bchange password\b|\bchange email\b|\bchange security\b|\bupdate (?:payment|billing|card)\b",
        r"\bsend\b|\bsubmit\b|\bpublish\b|\bpost\b|\bshare\b|\btransfer\b|\bwithdraw\b",
        r"\bsign out\b|\blog out\b|\bunsubscribe\b|\bcancel subscription\b",
    )
)


def consequential_signal(*texts: str) -> str:
    """Return a human-readable reason if any visible text implies a high-impact action."""
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        for pattern in _CONSEQUENTIAL_PATTERNS:
            if pattern.search(text):
                return f"consequential action signal: {pattern.pattern}"
    return ""


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------
class SessionState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class PageElement:
    """A bounded, safe view of one interactive element on the current page.

    Carries only non-secret, non-executable metadata. Values of credential
    fields are never populated.
    """

    element_id: str
    role: str = ""
    accessible_name: str = ""
    visible_text: str = ""
    tag: str = ""
    selector: str = ""
    input_type: str = ""
    value: str = ""
    disabled: bool = False
    options: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "role": self.role,
            "accessible_name": redact_secret(self.accessible_name)[:256],
            "visible_text": redact_secret(self.visible_text)[:256],
            "tag": self.tag,
            "selector": self.selector,
            "input_type": self.input_type,
            # Values of credential fields are never exposed, and any value is
            # redacted defensively so a secret can never ride into context.
            "value": "" if is_credential_field(input_type=self.input_type) else redact_secret(self.value)[:256],
            "disabled": self.disabled,
            "options": tuple(self.options[:50]),
        }


@dataclass(frozen=True)
class PageObservation:
    """A bounded snapshot of the current page. All text is untrusted data."""

    url: str
    title: str = ""
    status_code: int | None = None
    text: str = ""
    elements: tuple[PageElement, ...] = ()
    links: tuple[dict[str, str], ...] = ()
    redirect_depth: int = 0
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": redact_secret(self.title)[:256],
            "status_code": self.status_code,
            "text": redact_secret(self.text)[:MAX_TEXT_LENGTH],
            "elements": [el.safe_dict() for el in self.elements[:MAX_ELEMENTS]],
            "links": [dict(link) for link in self.links[:MAX_LINKS]],
            "redirect_depth": self.redirect_depth,
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class ElementTarget:
    """A resolved semantic target bound to a specific page epoch.

    A target is only valid on the page epoch it was resolved against; using it
    after the page changed is a stale target and fails closed.
    """

    element_id: str
    selector: str
    role: str
    accessible_name: str
    epoch: int

    def safe_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "selector": self.selector,
            "role": self.role,
            "accessible_name": redact_secret(self.accessible_name)[:256],
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class DownloadRecord:
    """Evidence for one completed, verified download."""

    url: str
    host: str
    filename: str
    relative_path: str
    size_bytes: int
    sha256: str
    status_code: int | None = None
    verified: bool = False

    def safe_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "filename": self.filename,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "status_code": self.status_code,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class NavigationRecord:
    """One entry in the bounded session history stack."""

    url: str
    status_code: int | None = None
    redirect_depth: int = 0


@dataclass(frozen=True)
class SessionSnapshot:
    """Serializable, secret-free snapshot used for checkpoint/resume.

    Contains only bounded navigation metadata and completed-action digests --
    never page text, never credentials.
    """

    session_id: str
    state: SessionState
    history: tuple[NavigationRecord, ...]
    current_index: int
    epoch: int
    action_count: int
    navigation_count: int
    completed_actions: tuple[str, ...]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "history": [
                {"url": r.url, "status_code": r.status_code, "redirect_depth": r.redirect_depth}
                for r in self.history
            ],
            "current_index": self.current_index,
            "epoch": self.epoch,
            "action_count": self.action_count,
            "navigation_count": self.navigation_count,
            "completed_actions": list(self.completed_actions),
        }


@dataclass
class ActionBudget:
    """Enforces an upper bound on the number of browser actions in a session."""

    limit: int = MAX_ACTION_BUDGET
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("action budget limit must be positive")

    def consume(self, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("action count must be positive")
        if self.used + count > self.limit:
            raise BrowserError(
                f"browser action budget exceeded: attempted {self.used + count}, limit is {self.limit}"
            )
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


__all__ = [
    "ActionBudget",
    "BackendUnavailableError",
    "BrowserError",
    "BrowserReplayError",
    "BrowserSecurityError",
    "DownloadError",
    "DownloadRecord",
    "ElementTarget",
    "MAX_ACTION_BUDGET",
    "MAX_DOWNLOAD_BYTES",
    "MAX_ELEMENTS",
    "MAX_LINKS",
    "MAX_NAVIGATIONS",
    "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES",
    "MAX_SELECTOR_LENGTH",
    "MAX_TEXT_LENGTH",
    "MAX_TYPED_LENGTH",
    "MAX_URL_LENGTH",
    "MAX_WORKFLOW_STEPS",
    "NavigationError",
    "NavigationRecord",
    "PageElement",
    "PageObservation",
    "REDACTED",
    "SessionError",
    "SessionSnapshot",
    "SessionState",
    "TargetResolutionError",
    "consequential_signal",
    "is_credential_field",
    "looks_like_secret",
    "redact_secret",
]
