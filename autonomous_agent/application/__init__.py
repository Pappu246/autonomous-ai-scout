"""Bounded application adapter domain (Phase 4).

Provides bounded, safe application adapter architecture, data models,
backend implementations, sessions, target resolution, replay protection,
and platform-independent security policy.

Guarantees:
- Fail-closed execution and strict input validation.
- Denylisted system binaries, shells, and dangerous scripting environments.
- Dangerous debug, inspector, eval, and sandbox-disabling flags rejected.
- Workspace path confinement and path traversal protection.
- Credential detection and redaction.
- Bounded payload sizes, argument lengths, and action budgets.
- Replay protection preventing repeated mutations upon resume.
- Approval gating for consequential operations.
"""

from __future__ import annotations

from .backend import (
    BaseApplicationBackend,
    MockApplicationBackend,
    UnsupportedApplicationBackend,
)
from .connector import BoundedApplicationConnector
from .observer import ApplicationPostConditionObserver
from .models import (
    ActionBudget,
    ActionBudgetExceededError,
    AdapterType,
    AdapterUnavailableError,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationError,
    ApplicationNotFoundError,
    ApplicationObservation,
    ApplicationReplayError,
    ApplicationSecurityError,
    ApplicationSessionError,
    ApplicationSessionSnapshot,
    ApplicationState,
    ApplicationTarget,
    BackendUnavailableError,
    MAX_ACTION_BUDGET,
    MAX_APP_NAME_LENGTH,
    MAX_ARGV_COUNT,
    MAX_ARG_LENGTH,
    MAX_COMMAND_LENGTH,
    MAX_DOCUMENT_PATH_LENGTH,
    MAX_SESSIONS,
    MAX_TEXT_PAYLOAD_LENGTH,
    MAX_WORKFLOW_STEPS,
    REDACTED,
    TargetResolutionError,
    consequential_signal,
    looks_like_secret,
    redact_secret,
)
from .policy import (
    ALLOWED_ADAPTER_COMMANDS,
    DANGEROUS_FLAGS,
    DENYLISTED_EXECUTABLES,
    FORBIDDEN_METACHARS,
    assert_not_denylisted,
    assert_safe_flag,
    confine_document_path,
    confine_workspace_path,
    is_denylisted_executable,
    validate_adapter_command,
    validate_app_launch,
    validate_application_name,
    validate_command_arguments,
    validate_text_payload,
)
from .replay import ApplicationReplayProtector
from .session import ApplicationSession
from .target import ApplicationSemanticTargetResolver

__all__ = [
    "ALLOWED_ADAPTER_COMMANDS",
    "ActionBudget",
    "ActionBudgetExceededError",
    "AdapterType",
    "AdapterUnavailableError",
    "ApplicationCommand",
    "ApplicationDescriptor",
    "ApplicationError",
    "ApplicationNotFoundError",
    "ApplicationObservation",
    "ApplicationPostConditionObserver",
    "ApplicationReplayError",
    "ApplicationReplayProtector",
    "ApplicationSecurityError",
    "ApplicationSemanticTargetResolver",
    "ApplicationSession",
    "ApplicationSessionError",
    "ApplicationSessionSnapshot",
    "ApplicationState",
    "ApplicationTarget",
    "BackendUnavailableError",
    "BaseApplicationBackend",
    "BoundedApplicationConnector",
    "DANGEROUS_FLAGS",
    "DENYLISTED_EXECUTABLES",
    "FORBIDDEN_METACHARS",
    "MAX_ACTION_BUDGET",
    "MAX_APP_NAME_LENGTH",
    "MAX_ARGV_COUNT",
    "MAX_ARG_LENGTH",
    "MAX_COMMAND_LENGTH",
    "MAX_DOCUMENT_PATH_LENGTH",
    "MAX_SESSIONS",
    "MAX_TEXT_PAYLOAD_LENGTH",
    "MAX_WORKFLOW_STEPS",
    "MockApplicationBackend",
    "REDACTED",
    "TargetResolutionError",
    "UnsupportedApplicationBackend",
    "assert_not_denylisted",
    "assert_safe_flag",
    "confine_document_path",
    "confine_workspace_path",
    "consequential_signal",
    "is_denylisted_executable",
    "looks_like_secret",
    "redact_secret",
    "validate_adapter_command",
    "validate_app_launch",
    "validate_application_name",
    "validate_command_arguments",
    "validate_text_payload",
]
