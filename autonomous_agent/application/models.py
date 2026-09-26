"""Core models, exceptions, action budgets, data shapes, bounds, and redaction for bounded application adapters.

Nothing in this module launches or drives live applications. It defines the safe,
bounded data shapes and fail-closed exceptions used across the application domain
so that adapters, sessions, targets, observations and policy layers all speak
one vocabulary and refuse unsafe input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# --------------------------------------------------------------------------
# Exceptions -- fail-closed signals for application adapter domain.
# --------------------------------------------------------------------------
class ApplicationError(Exception):
    """Base exception for the application adapter domain."""


class ApplicationSecurityError(ApplicationError):
    """Raised when an operation violates application safety boundaries or security policy."""


class ApplicationNotFoundError(ApplicationError):
    """Raised when a requested application adapter is not registered or found."""


class ApplicationSessionError(ApplicationError):
    """Raised when an operation requires an open, valid application session."""


class ApplicationReplayError(ApplicationError):
    """Raised when a resume would blindly repeat a completed mutating action."""


class TargetResolutionError(ApplicationError):
    """Raised when a semantic application target cannot be resolved or is stale."""


class ActionBudgetExceededError(ApplicationError):
    """Raised when an operation attempts to exceed the allocated action budget."""


class BackendUnavailableError(ApplicationError):
    """Raised when no real application adapter backend is available in this environment."""


class AdapterUnavailableError(BackendUnavailableError):
    """Alias for backwards compatibility with M1."""


# --------------------------------------------------------------------------
# Bounds -- upper bounds to prevent resource exhaustion and unbounded growth.
# --------------------------------------------------------------------------
MAX_APP_NAME_LENGTH = 128
MAX_COMMAND_LENGTH = 512
MAX_ARGV_COUNT = 30
MAX_ARG_LENGTH = 1024
MAX_TEXT_PAYLOAD_LENGTH = 65_536
MAX_DOCUMENT_PATH_LENGTH = 1024
MAX_ACTION_BUDGET = 50
MAX_SESSIONS = 10
MAX_WORKFLOW_STEPS = 25


# --------------------------------------------------------------------------
# Credential material -- detection and redaction
# --------------------------------------------------------------------------
_CREDENTIAL_MATERIAL = re.compile(
    r"(?i)((?:\b|[_-])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"authorization|password|passwd|secret|private[_-]?key|"
    r"client[_-]?secret|credentials?)\s*[:=]\s*)\S+"
)
_BEARER_MATERIAL = re.compile(r"(?i)(bearer\s+)\S+")
REDACTED = "[REDACTED]"


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


# --------------------------------------------------------------------------
# Consequential action detection -- content-based gate on sensitive operations
# --------------------------------------------------------------------------
_CONSEQUENTIAL_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bpay\b|\bpayment\b|\bcheckout\b|\bbuy\b|\bpurchase\b|\bbilling\b",
        r"\bdelete\b|\bdelete account\b|\bremove account\b|\bclose account\b|\bdeactivate\b",
        r"\bchange password\b|\bchange email\b|\bchange security\b|\bexport credentials\b",
        r"\bpublish\b|\bshare\b|\btransfer\b|\bwithdraw\b|\bformat\b|\bshutdown\b",
        r"\bdrop\b|\btruncate\b|\bwipe\b|\bdestroy\b|\bpurge\b|\buninstall\b",
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
# Enums and Data Shapes
# --------------------------------------------------------------------------
class ApplicationState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    BUSY = "busy"
    SUSPENDED = "suspended"
    ERROR = "error"


class AdapterType(str, Enum):
    OFFICE = "office"
    EDITOR = "editor"
    IDE = "ide"
    GRAPHICS = "graphics"
    COMMUNICATION = "communication"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ApplicationDescriptor:
    """Declared description of an application adapter."""

    app_id: str
    name: str
    adapter_type: AdapterType
    executable: str = ""
    supported_extensions: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    requires_approval: bool = True
    safe_autonomous: bool = False
    description: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "name": redact_secret(self.name)[:MAX_APP_NAME_LENGTH],
            "adapter_type": self.adapter_type.value if isinstance(self.adapter_type, AdapterType) else str(self.adapter_type),
            "executable": self.executable,
            "supported_extensions": tuple(self.supported_extensions),
            "allowed_commands": tuple(self.allowed_commands),
            "requires_approval": self.requires_approval,
            "safe_autonomous": self.safe_autonomous,
            "description": redact_secret(self.description)[:256],
        }


@dataclass(frozen=True)
class ApplicationTarget:
    """A resolved target inside an application."""

    target_id: str
    app_id: str
    document_path: str = ""
    view_name: str = ""
    control_id: str = ""
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "app_id": self.app_id,
            "document_path": redact_secret(self.document_path)[:MAX_DOCUMENT_PATH_LENGTH],
            "view_name": redact_secret(self.view_name)[:128],
            "control_id": redact_secret(self.control_id)[:128],
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class ApplicationCommand:
    """A validated, bounded command for an application adapter."""

    command: str
    app_id: str
    args: tuple[str, ...] = ()
    payload: str = ""
    target: ApplicationTarget | None = None
    timeout_seconds: float = 30.0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "app_id": self.app_id,
            "args": tuple(redact_secret(arg)[:MAX_ARG_LENGTH] for arg in self.args[:MAX_ARGV_COUNT]),
            "payload": redact_secret(self.payload)[:MAX_TEXT_PAYLOAD_LENGTH],
            "target": self.target.safe_dict() if self.target is not None else None,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class ApplicationObservation:
    """A bounded snapshot of the application state."""

    app_id: str
    session_id: str = ""
    state: ApplicationState = ApplicationState.CLOSED
    active_document: str = ""
    status_message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "session_id": self.session_id,
            "state": self.state.value if isinstance(self.state, ApplicationState) else str(self.state),
            "active_document": redact_secret(self.active_document)[:MAX_DOCUMENT_PATH_LENGTH],
            "status_message": redact_secret(self.status_message)[:256],
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class ApplicationSessionSnapshot:
    """Serializable, secret-free snapshot of an application session."""

    session_id: str
    app_id: str
    state: ApplicationState
    active_document: str
    epoch: int
    action_count: int
    completed_actions: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "app_id": self.app_id,
            "state": self.state.value if isinstance(self.state, ApplicationState) else str(self.state),
            "active_document": redact_secret(self.active_document)[:MAX_DOCUMENT_PATH_LENGTH],
            "epoch": self.epoch,
            "action_count": self.action_count,
            "completed_actions": list(self.completed_actions),
        }


@dataclass
class ActionBudget:
    """Enforces an upper bound on application adapter actions in a session."""

    limit: int = MAX_ACTION_BUDGET
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("action budget limit must be positive")

    def consume(self, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("action count must be positive")
        if self.used + count > self.limit:
            raise ActionBudgetExceededError(
                f"application action budget exceeded: attempted {self.used + count}, limit is {self.limit}"
            )
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def reset(self) -> None:
        self.used = 0


__all__ = [
    "ActionBudget",
    "ActionBudgetExceededError",
    "AdapterType",
    "AdapterUnavailableError",
    "ApplicationCommand",
    "ApplicationDescriptor",
    "ApplicationError",
    "ApplicationNotFoundError",
    "ApplicationObservation",
    "ApplicationReplayError",
    "ApplicationSecurityError",
    "ApplicationSessionError",
    "ApplicationSessionSnapshot",
    "ApplicationState",
    "ApplicationTarget",
    "BackendUnavailableError",
    "MAX_ACTION_BUDGET",
    "MAX_APP_NAME_LENGTH",
    "MAX_ARGV_COUNT",
    "MAX_ARG_LENGTH",
    "MAX_COMMAND_LENGTH",
    "MAX_DOCUMENT_PATH_LENGTH",
    "MAX_SESSIONS",
    "MAX_TEXT_PAYLOAD_LENGTH",
    "MAX_WORKFLOW_STEPS",
    "REDACTED",
    "TargetResolutionError",
    "consequential_signal",
    "looks_like_secret",
    "redact_secret",
]
