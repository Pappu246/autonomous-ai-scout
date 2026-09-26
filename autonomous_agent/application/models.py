"""Non-executable contracts for allowlisted application adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class ApplicationError(ValueError):
    """Base error for invalid application-domain data."""


class InvocationKind(str, Enum):
    OBSERVE = "observe"
    INTERACT = "interact"
    CONSEQUENT = "consequent"


@dataclass(frozen=True)
class ApplicationManifest:
    """Metadata for one explicitly registered adapter; no executable command."""

    adapter_id: str
    display_name: str
    vendor: str = ""
    supported_operations: tuple[str, ...] = ()
    allowlisted: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.adapter_id or len(self.adapter_id) > 128:
            raise ApplicationError("adapter id must be bounded and non-empty")
        if not self.display_name or len(self.display_name) > 256:
            raise ApplicationError("application display name is invalid")
        if not self.allowlisted:
            raise ApplicationError("application adapters must be explicitly allowlisted")
        if len(self.supported_operations) > 128 or any(
            not operation or len(operation) > 128 for operation in self.supported_operations
        ):
            raise ApplicationError("application operations are invalid")


@dataclass(frozen=True)
class ApplicationRequest:
    """A bounded adapter request with no shell, process, JS, or debugger fields."""

    adapter_id: str
    operation: str
    arguments: Mapping[str, str] = field(default_factory=dict)
    invocation: InvocationKind = InvocationKind.OBSERVE
    approved: bool = False

    def __post_init__(self) -> None:
        if not self.adapter_id or not self.operation:
            raise ApplicationError("adapter and operation are required")
        if len(self.arguments) > 64:
            raise ApplicationError("application arguments are too large")
        if any(len(str(key)) > 128 or len(str(value)) > 16_384 for key, value in self.arguments.items()):
            raise ApplicationError("application argument is oversized")
        if self.invocation is InvocationKind.CONSEQUENT and not self.approved:
            raise ApplicationError("consequential application invocation requires approval")


__all__ = ["ApplicationError", "ApplicationManifest", "ApplicationRequest", "InvocationKind"]
