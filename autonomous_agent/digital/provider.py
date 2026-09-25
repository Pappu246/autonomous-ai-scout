"""Concrete capability adapters bound to the existing safe execution boundary.

Nothing here invents a second executor. A :class:`RegisteredToolCapability`
pairs one already-registered :class:`~autonomous_agent.tool_registry.ToolSpec`
with the existing sandbox operation that implements it, so the digital layer
adds *routing, lifecycle and verification* without adding new execution power.

Authorization is delegated to the process tool registry. The capability has no
way to widen its own grants: ``authorize()`` is a query, never a decision
source, and :mod:`autonomous_agent.digital.runtime` re-checks the same registry
immediately before executing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Mapping, Protocol

from ..capability_policy import Capability, CapabilityDecision
from ..sandbox import MAX_OUTPUT_BYTES, SandboxResult, run_safe_operation
from ..tool_registry import REGISTRY, ToolRegistry, ToolSpec
from .contract import (
    CapabilityAuditRecord,
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityExecution,
    CapabilityObservation,
    CapabilityOutcome,
    CapabilityRequest,
    CapabilityVerification,
    InputValidation,
    RetryPolicy,
)
from .domains import CapabilityDomain


_SECRET_MATERIAL = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]\s*)\S+"
)

REDACTED = "[REDACTED]"


def redact_secret_material(value: Any) -> Any:
    """Recursively strip credential-looking ``key=value`` pairs from evidence.

    Connectors already redact their own payloads; this is the second, layer-wide
    guarantee so capability evidence can never smuggle a secret into model
    context or an audit record.
    """
    if isinstance(value, str):
        return _SECRET_MATERIAL.sub(r"\1" + REDACTED, value)
    if isinstance(value, Mapping):
        return {str(key): redact_secret_material(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_secret_material(item) for item in value)  # type: ignore[call-arg]
    return value


class CapabilityExecutor(Protocol):
    """Runs one bound tool through an existing safe boundary."""

    def run(self, tool_name: str, arguments: Mapping[str, Any]) -> SandboxResult: ...


class CapabilityObserver(Protocol):
    """Independently confirms a real-world post-condition."""

    def observe(
        self, request: CapabilityRequest, execution: CapabilityExecution
    ) -> CapabilityObservation: ...


AuditSink = Callable[[Mapping[str, str]], None]


# tool name -> (sandbox operation, connector slot, sandbox operation key)
#
# This table is the only place the digital layer learns how a registered tool
# maps onto the pre-existing sandbox allowlist. Adding a capability means adding
# a row here plus a registration entry; no planner or engine changes.
TOOL_SANDBOX_BINDINGS: Mapping[str, tuple[str, str | None, str | None]] = {
    "filesystem.list": ("filesystem_workspace", "workspace", "list"),
    "filesystem.read": ("filesystem_workspace", "workspace", "read"),
    "filesystem.write": ("filesystem_workspace", "workspace", "write"),
    "filesystem.transform": ("filesystem_workspace", "workspace", "transform"),
    "workspace.shell": ("workspace_shell", "workspace", None),
    "web.search": ("web_research", "web", "search"),
    "web.read": ("web_research", "web", "read"),
    "web.extract": ("web_research", "web", "extract"),
    "web.compare": ("web_research", "web", "compare"),
    "email.search": ("gmail", "gmail", "search"),
    "email.read": ("gmail", "gmail", "read"),
    "email.thread": ("gmail", "gmail", "thread"),
    "email.draft": ("gmail", "gmail", "draft"),
    "email.send": ("gmail", "gmail", "send"),
    "calendar.read": ("calendar", "calendar", "read"),
    "calendar.list": ("calendar", "calendar", "list"),
    "calendar.find_free_time": ("calendar", "calendar", "find_free_time"),
    "calendar.event.create": ("calendar", "calendar", "create"),
    "calendar.event.update": ("calendar", "calendar", "update"),
    "calendar.event.cancel": ("calendar", "calendar", "cancel"),
    "browser.open": ("browser", "browser", "open"),
    "browser.click": ("browser", "browser", "click"),
    "browser.extract": ("browser", "browser", "extract"),
    "computer.screen.capture": ("computer", "computer", "screen_capture"),
    "computer.window.list": ("computer", "computer", "window_list"),
    "computer.window.active": ("computer", "computer", "window_active"),
    "computer.window.focus": ("computer", "computer", "window_focus"),
    "computer.app.launch": ("computer", "computer", "app_launch"),
    "computer.mouse.move": ("computer", "computer", "mouse_move"),
    "computer.mouse.click": ("computer", "computer", "mouse_click"),
    "computer.keyboard.type": ("computer", "computer", "keyboard_type"),
    "computer.keyboard.hotkey": ("computer", "computer", "keyboard_hotkey"),
    "computer.clipboard.read": ("computer", "computer", "clipboard_read"),
    "computer.clipboard.write": ("computer", "computer", "clipboard_write"),
    "github.inspect": ("inspect", None, None),
    "tests.run": ("test", None, None),
    "lint.run": ("lint", None, None),
    "metrics.collect": ("metrics", None, None),
}


def parse_evidence(text: str) -> Mapping[str, Any]:
    """Turn a sandbox payload into structured, redacted evidence."""
    cleaned = redact_secret_material(text)
    if isinstance(cleaned, str) and cleaned.strip().startswith(("{", "[")):
        try:
            decoded = json.loads(cleaned)
        except (TypeError, ValueError):
            return {"output": cleaned}
        if isinstance(decoded, Mapping):
            return {str(key): value for key, value in decoded.items()}
        return {"output": decoded}
    return {"output": cleaned}


class SandboxCapabilityExecutor:
    """Execute a bound tool through :func:`run_safe_operation`.

    This is the *existing* sandbox: same allowlist, same root confinement, same
    network isolation, same output caps. The digital layer only chooses which
    allowlisted operation and which already-approved connector to use.
    """

    def __init__(
        self,
        root: Any,
        *,
        connectors: Mapping[str, Any] | None = None,
        timeout_seconds: int = 30,
        output_limit: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self._root = root
        self._connectors = dict(connectors or {})
        self._timeout_seconds = timeout_seconds
        self._output_limit = output_limit

    def run(self, tool_name: str, arguments: Mapping[str, Any]) -> SandboxResult:
        binding = TOOL_SANDBOX_BINDINGS.get(tool_name)
        if binding is None:
            raise CapabilityError(f"tool has no sandbox binding: {tool_name}")
        operation, slot, operation_key = binding
        payload: dict[str, Any] = dict(arguments)
        if operation_key is not None:
            payload["operation"] = operation_key
        kwargs: dict[str, Any] = {}
        if slot is not None:
            kwargs[f"{slot}_connector"] = self._connectors.get(slot)
            kwargs[f"{slot}_request"] = payload
        return run_safe_operation(
            operation,
            self._root,
            timeout_seconds=self._timeout_seconds,
            output_limit=self._output_limit,
            **kwargs,
        )


class BaseDigitalCapability:
    """Shared lifecycle implementation for capability adapters."""

    def __init__(
        self,
        *,
        descriptor: CapabilityDescriptor,
        executor: CapabilityExecutor | None = None,
        tool_registry: ToolRegistry = REGISTRY,
        observer: CapabilityObserver | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        spec = tool_registry.get(descriptor.tool_name)
        if spec is None:
            raise CapabilityError(
                f"capability tool is not registered: {descriptor.tool_name}"
            )
        if spec.capability != descriptor.capability:
            raise CapabilityError(
                f"capability {descriptor.capability_id} declares "
                f"{descriptor.capability} but the registered tool declares "
                f"{spec.capability}"
            )
        if descriptor.availability is CapabilityAvailability.AVAILABLE and executor is None:
            raise CapabilityError(
                f"available capability requires an executor: {descriptor.capability_id}"
            )
        self._descriptor = descriptor
        self._spec = spec
        self._executor = executor
        self._tool_registry = tool_registry
        self._observer = observer
        self._audit_sink = audit_sink

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    # -- discover ---------------------------------------------------------
    def discover(self) -> CapabilityDescriptor:
        return self._descriptor

    # -- validate_input ---------------------------------------------------
    def validate_input(self, arguments: Mapping[str, Any]) -> InputValidation:
        """Validate against the *registered tool's* input schema.

        The registered schema is the authority, so a capability cannot accept
        arguments its tool would reject.
        """
        if not isinstance(arguments, Mapping):
            return InputValidation(False, "capability arguments must be an object")
        schema = self._spec.input_schema
        if schema.get("type") != "object":
            return InputValidation(True, "schema is not an object schema", dict(arguments))
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return InputValidation(False, "registered input schema is invalid")
        required = schema.get("required", ())
        if isinstance(required, (list, tuple)):
            missing = sorted(str(name) for name in required if name not in arguments)
            if missing:
                return InputValidation(False, f"missing required arguments: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(arguments) - set(properties))
            if unknown:
                return InputValidation(False, f"unknown arguments: {', '.join(unknown)}")
        for name, value in arguments.items():
            rule = properties.get(name)
            if not isinstance(rule, Mapping) or "type" not in rule:
                continue
            expected = rule.get("type")
            valid = {
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
                "array": isinstance(value, (list, tuple)),
                "object": isinstance(value, Mapping),
            }.get(expected, True)
            if not valid:
                return InputValidation(False, f"argument '{name}' must be of type {expected}")
        return InputValidation(True, "arguments match the registered tool schema", dict(arguments))

    # -- authorize (delegated, never self-granted) ------------------------
    def authorize(
        self,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
    ) -> CapabilityDecision:
        """Query the tool registry. This method cannot grant anything itself."""
        return self._tool_registry.authorize(
            self._descriptor.tool_name,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )

    # -- execute ----------------------------------------------------------
    def execute(self, request: CapabilityRequest) -> CapabilityExecution:
        if self._executor is None:
            return CapabilityExecution(
                request.capability_id,
                False,
                error="capability has no registered executor",
                boundary="none",
            )
        if request.capability_id != self._descriptor.capability_id:
            return CapabilityExecution(
                request.capability_id,
                False,
                error="request capability id does not match this capability",
                boundary="none",
            )
        validation = self.validate_input(request.arguments)
        if not validation.ok:
            return CapabilityExecution(
                request.capability_id, False, error=validation.reason, boundary="none"
            )
        # Schema validation applies to caller-supplied arguments only. The
        # approval flag is trusted runtime state, appended after validation so
        # a model can never supply it and so registered schemas stay intact.
        payload = dict(request.arguments)
        if not self._spec.safe_autonomous:
            payload["approved"] = bool(request.approved)
        try:
            result = self._executor.run(self._descriptor.tool_name, payload)
        except CapabilityError as exc:
            return CapabilityExecution(request.capability_id, False, error=str(exc), boundary="sandbox")
        except Exception as exc:  # boundary failures must not escape as exceptions
            return CapabilityExecution(
                request.capability_id,
                False,
                error=f"{type(exc).__name__}",
                boundary="sandbox",
            )
        boundary_verified = bool(result.success) and result.verification_status == "verified"
        return CapabilityExecution(
            request.capability_id,
            boundary_verified,
            evidence=parse_evidence(result.output) if boundary_verified else {},
            output=redact_secret_material(result.output)
            if isinstance(result.output, str)
            else str(result.output),
            error="" if boundary_verified else result.output,
            exit_status=result.exit_status,
            output_truncated=result.output_truncated,
            boundary=result.operation,
        )

    # -- observe ----------------------------------------------------------
    def observe(
        self, request: CapabilityRequest, execution: CapabilityExecution
    ) -> CapabilityObservation:
        if not execution.success:
            return CapabilityObservation(
                request.capability_id, False, detail=execution.error or "execution did not succeed"
            )
        if not execution.has_evidence:
            # A successful call that produced nothing checkable is a no-op and
            # must never be promoted to verified.
            return CapabilityObservation(
                request.capability_id,
                False,
                detail="execution produced no observable evidence",
            )
        if self._observer is not None:
            return self._observer.observe(request, execution)
        return CapabilityObservation(
            request.capability_id,
            True,
            evidence=execution.evidence,
            detail="boundary-reported evidence",
        )

    # -- verify -----------------------------------------------------------
    def verify(
        self,
        request: CapabilityRequest,
        execution: CapabilityExecution,
        observation: CapabilityObservation,
    ) -> CapabilityVerification:
        evidence_present = execution.has_evidence and observation.has_evidence
        if not execution.success:
            return CapabilityVerification(
                False, execution.error or "capability execution failed", evidence_present
            )
        if not observation.observed:
            return CapabilityVerification(
                False,
                observation.detail or "post-execution observation failed",
                evidence_present,
            )
        if not evidence_present:
            return CapabilityVerification(
                False,
                "verification requires observable evidence; no-op results are not verified",
                evidence_present,
            )
        return CapabilityVerification(
            True, "capability result was observed and verified", evidence_present, observation.detail
        )

    # -- bounded_retry ----------------------------------------------------
    def bounded_retry(self, request: CapabilityRequest) -> CapabilityOutcome:
        """Run the execute/observe/verify loop within the declared budget.

        A step that has already produced verified evidence is never re-run, so
        resume cannot replay verified work.
        """
        attempts = max(1, int(self._descriptor.retry_policy.max_attempts))
        execution: CapabilityExecution | None = None
        observation: CapabilityObservation | None = None
        verification: CapabilityVerification | None = None
        for attempt in range(1, attempts + 1):
            execution = self.execute(request)
            observation = self.observe(request, execution)
            verification = self.verify(request, execution, observation)
            execution = CapabilityExecution(
                execution.capability_id,
                execution.success,
                execution.evidence,
                execution.output,
                execution.error,
                execution.exit_status,
                execution.output_truncated,
                execution.boundary,
                attempt,
            )
            if verification.verified:
                return CapabilityOutcome(
                    request.capability_id,
                    self._descriptor.tool_name,
                    "verified",
                    verification.reason,
                    attempt,
                    execution,
                    observation,
                    verification,
                )
        assert execution is not None and observation is not None and verification is not None
        return CapabilityOutcome(
            request.capability_id,
            self._descriptor.tool_name,
            "failed",
            verification.reason or "bounded retries exhausted without verification",
            attempts,
            execution,
            observation,
            verification,
        )

    # -- audit ------------------------------------------------------------
    def bind_audit_sink(self, sink: AuditSink | None) -> None:
        """Attach the runtime-owned audit destination.

        The capability never chooses where its records go and never rewrites
        them; the sink writes into the hash-chained execution audit.
        """
        self._audit_sink = sink

    def audit(self, record: CapabilityAuditRecord) -> None:
        """Hand the record to the injected sink; never mutate it.

        The sink is owned by the runtime and writes to the hash-chained
        execution audit, which keeps audit records trustworthy.
        """
        if self._audit_sink is None:
            return
        self._audit_sink(record.as_record())


class RegisteredToolCapability(BaseDigitalCapability):
    """A capability backed by one tool that already exists in the registry."""

    @classmethod
    def from_tool(
        cls,
        tool_name: str,
        *,
        domain: CapabilityDomain | str,
        capability_id: str,
        signals: Iterable[str] = (),
        stage: int = 50,
        retry_policy: RetryPolicy | None = None,
        availability: CapabilityAvailability = CapabilityAvailability.AVAILABLE,
        credential_handling: str = "none",
        executor: CapabilityExecutor | None = None,
        tool_registry: ToolRegistry = REGISTRY,
        observer: CapabilityObserver | None = None,
        audit_sink: AuditSink | None = None,
        notes: str = "",
    ) -> "RegisteredToolCapability":
        spec = tool_registry.get(tool_name)
        if spec is None:
            raise CapabilityError(f"tool is not registered: {tool_name}")
        descriptor = CapabilityDescriptor(
            capability_id=capability_id,
            domain=CapabilityDomain(domain) if isinstance(domain, str) else domain,
            tool_name=tool_name,
            description=spec.description,
            capability=spec.capability,
            risk=spec.risk_level.value,
            read_write=spec.read_write_mode.value,
            network=spec.network_requirement.value,
            approval=spec.approval_requirement.value,
            sandbox=spec.sandbox_requirement.value,
            audit=spec.audit_requirement.value,
            safe_autonomous=spec.safe_autonomous,
            availability=availability,
            signals=tuple(signals),
            stage=stage,
            retry_policy=retry_policy or RetryPolicy(),
            credential_handling=credential_handling,
            notes=notes,
        )
        return cls(
            descriptor=descriptor,
            executor=executor,
            tool_registry=tool_registry,
            observer=observer,
            audit_sink=audit_sink,
        )


__all__ = [
    "AuditSink",
    "BaseDigitalCapability",
    "CapabilityExecutor",
    "CapabilityObserver",
    "REDACTED",
    "RegisteredToolCapability",
    "SandboxCapabilityExecutor",
    "TOOL_SANDBOX_BINDINGS",
    "parse_evidence",
    "redact_secret_material",
]
