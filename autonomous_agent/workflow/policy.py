"""Platform-independent security policy, DAG validation, trust propagation and approval gating for bounded cross-domain workflows.

Every function fails closed: on any doubt, unknown identifier, cycle,
understated effect, untrusted taint or missing approval it raises a
:class:`~autonomous_agent.workflow.models.WorkflowError` subclass rather than
letting the workflow proceed.

The policy enforces six invariants that Phase 4's per-domain policies cannot
express on their own, because they are properties of a *composition* of domains
rather than of a single domain:

1. **Bounded acyclic structure.** A workflow is a DAG with bounded steps,
   dependencies, handoffs and domains. Cycles are rejected, never unrolled.
2. **Declared, ordered handoffs.** Data only crosses a domain boundary through
   a handoff whose consumer transitively depends on its producer, and only for
   artifact keys both sides declared.
3. **Trust propagation.** Untrusted content taints every downstream step. Trust
   never silently upgrades by passing through another domain.
4. **Hard sinks.** External content can never reach the shell or desktop input
   domains, with or without approval.
5. **No understated effects.** A step whose operation implies mutation cannot
   declare itself read-only to slip past the approval gate.
6. **Secret-free definitions.** Credential material never appears in a workflow
   declaration; credentials come from the broker at execution time.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from ..digital.domains import CapabilityDomain, DomainPhase, coerce_domain, domain_descriptor
from ..prompt_injection_guard import TrustLevel
from .models import (
    MAX_ARTIFACT_KEY_LENGTH,
    MAX_ARTIFACT_KEYS,
    MAX_CAPABILITY_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_METADATA_ENTRIES,
    MAX_OPERATION_LENGTH,
    MAX_PARAMETER_DEPTH,
    MAX_PARAMETER_ITEMS,
    MAX_PARAMETER_KEY_LENGTH,
    MAX_PARAMETER_VALUE_LENGTH,
    MAX_STEP_ACTION_COST,
    MAX_STEP_DEPENDENCIES,
    MAX_STEP_ID_LENGTH,
    MAX_STEP_PARAMETERS,
    MAX_WORKFLOW_ACTION_BUDGET,
    MAX_WORKFLOW_DOMAINS,
    MAX_WORKFLOW_GOAL_LENGTH,
    MAX_WORKFLOW_HANDOFFS,
    MAX_WORKFLOW_ID_LENGTH,
    MAX_WORKFLOW_NAME_LENGTH,
    MAX_WORKFLOW_STEPS,
    HandoffKind,
    StepEffect,
    WorkflowApprovalError,
    WorkflowDependencyError,
    WorkflowDomainError,
    WorkflowHandoff,
    WorkflowHandoffError,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowState,
    WorkflowStep,
    WorkflowValidationError,
    consequential_signal,
    looks_like_secret,
    redact_secret,
)


# --------------------------------------------------------------------------
# Identifier grammars -- deterministic, lowercase, no whitespace or separators
# that could be reinterpreted by a downstream shell, path or URL parser.
# --------------------------------------------------------------------------
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_.]*$")
_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_.]*$")
_PARAMETER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


#: Operations a workflow step may never name, in any domain. Defence in depth:
#: even if some future backend exposed them, the workflow layer refuses first.
FORBIDDEN_STEP_OPERATIONS: frozenset[str] = frozenset({
    "eval",
    "evaluate",
    "exec",
    "execute_script",
    "run_script",
    "spawn",
    "popen",
    "system",
    "shell",
    "subprocess",
    "cdp",
    "debugger",
    "attach_debugger",
    "inject",
    "patch_policy",
    "disable_policy",
    "disable_approval",
    "bypass_approval",
    "grant_capability",
    "escalate",
})

#: Parameter names that would inline credential material into a definition.
#: Credentials come from the auth broker at execution time, never from a plan.
FORBIDDEN_PARAMETER_KEYS: frozenset[str] = frozenset({
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "password",
    "passwd",
    "secret",
    "private_key",
    "client_secret",
    "credential",
    "credentials",
    "session_cookie",
    "cookie",
})

#: Parameter names whose values are treated as workspace paths and confined.
PATH_PARAMETER_KEYS: frozenset[str] = frozenset({
    "path",
    "file",
    "file_path",
    "filepath",
    "input_path",
    "output_path",
    "source_path",
    "target_path",
    "document_path",
    "relative_path",
    "workspace_path",
    "directory",
    "folder",
})

#: Operation fragments that imply the step changes real-world state.
MUTATING_OPERATION_SIGNALS: tuple[str, ...] = (
    "write",
    "create",
    "update",
    "delete",
    "remove",
    "send",
    "submit",
    "publish",
    "transform",
    "merge",
    "deploy",
    "execute",
    "command",
    "click",
    "type",
    "upload",
    "download",
    "save",
    "close",
    "open_session",
    "launch",
    "move",
    "rename",
    "cancel",
    "approve",
    "pay",
)

#: Trust tier of content originating in each domain. Domains whose content is
#: authored by third parties are EXTERNAL and taint everything downstream.
DOMAIN_TRUST: Mapping[CapabilityDomain, TrustLevel] = {
    CapabilityDomain.WEB: TrustLevel.EXTERNAL,
    CapabilityDomain.BROWSER: TrustLevel.EXTERNAL,
    CapabilityDomain.EMAIL: TrustLevel.EXTERNAL,
    CapabilityDomain.DOCUMENTS: TrustLevel.EXTERNAL,
    CapabilityDomain.CALENDAR: TrustLevel.TOOL_RESULT,
    CapabilityDomain.FILESYSTEM: TrustLevel.TOOL_RESULT,
    CapabilityDomain.GITHUB: TrustLevel.TOOL_RESULT,
    CapabilityDomain.APPLICATION: TrustLevel.TOOL_RESULT,
    CapabilityDomain.COMPUTER: TrustLevel.TOOL_RESULT,
    CapabilityDomain.OS_SHELL: TrustLevel.TOOL_RESULT,
    CapabilityDomain.TESTING: TrustLevel.TOOL_RESULT,
    # Orchestration metadata is produced by this deterministic layer itself.
    CapabilityDomain.WORKFLOW: TrustLevel.TOOL_RESULT,
    # Message content is shaped by whatever fed it, so it stays untrusted.
    CapabilityDomain.COMMUNICATION: TrustLevel.EXTERNAL,
}

#: Domains whose produced content must be treated as untrusted external data.
UNTRUSTED_SOURCE_DOMAINS: frozenset[CapabilityDomain] = frozenset(
    domain for domain, trust in DOMAIN_TRUST.items() if trust is TrustLevel.EXTERNAL
)

#: Domains where a mutating step causes real-world side effects and therefore
#: requires an explicit human approval gate.
SENSITIVE_SINK_DOMAINS: frozenset[CapabilityDomain] = frozenset({
    CapabilityDomain.APPLICATION,
    CapabilityDomain.CALENDAR,
    CapabilityDomain.COMMUNICATION,
    CapabilityDomain.COMPUTER,
    CapabilityDomain.DOCUMENTS,
    CapabilityDomain.EMAIL,
    CapabilityDomain.FILESYSTEM,
    CapabilityDomain.GITHUB,
    CapabilityDomain.OS_SHELL,
})

#: Domains that untrusted external content may never reach, with or without
#: approval: command interpretation and synthetic desktop input are absolute
#: boundaries, not approval-gated conveniences.
BLOCKED_UNTRUSTED_SINK_DOMAINS: frozenset[CapabilityDomain] = frozenset({
    CapabilityDomain.OS_SHELL,
    CapabilityDomain.COMPUTER,
})

_TRUST_RANK: Mapping[TrustLevel, int] = {
    TrustLevel.SYSTEM: 4,
    TrustLevel.USER: 3,
    TrustLevel.MEMORY: 2,
    TrustLevel.TOOL_RESULT: 1,
    TrustLevel.EXTERNAL: 0,
}


# --------------------------------------------------------------------------
# Identifier and scalar validation
# --------------------------------------------------------------------------
def _clean_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise WorkflowValidationError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise WorkflowValidationError(f"{label} cannot be empty")
    if _CONTROL_CHARS.search(cleaned):
        raise WorkflowSecurityError(f"control characters are not permitted in {label}")
    return cleaned


def _validate_identifier(value: Any, label: str, max_length: int) -> str:
    cleaned = _clean_text(value, label)
    if len(cleaned) > max_length:
        raise WorkflowValidationError(
            f"{label} exceeds max length: {len(cleaned)} > {max_length}"
        )
    if not _ID_PATTERN.match(cleaned):
        raise WorkflowValidationError(
            f"{label} must be lowercase and may only contain letters, digits, '.', '_' and '-': {value!r}"
        )
    return cleaned


def validate_workflow_id(value: Any) -> str:
    """Validate a workflow identifier."""
    return _validate_identifier(value, "workflow id", MAX_WORKFLOW_ID_LENGTH)


def validate_step_id(value: Any) -> str:
    """Validate a step identifier."""
    return _validate_identifier(value, "step id", MAX_STEP_ID_LENGTH)


def validate_artifact_key(value: Any) -> str:
    """Validate a handoff artifact key."""
    return _validate_identifier(value, "artifact key", MAX_ARTIFACT_KEY_LENGTH)


def validate_workflow_name(value: Any) -> str:
    """Validate a human-readable workflow name."""
    cleaned = _clean_text(value, "workflow name")
    if len(cleaned) > MAX_WORKFLOW_NAME_LENGTH:
        raise WorkflowValidationError(
            f"workflow name exceeds max length: {len(cleaned)} > {MAX_WORKFLOW_NAME_LENGTH}"
        )
    assert_secret_free(cleaned, "workflow name")
    return cleaned


def validate_goal(value: Any) -> str:
    """Validate the optional workflow goal text."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise WorkflowValidationError("workflow goal must be a string")
    if _CONTROL_CHARS.search(value):
        raise WorkflowSecurityError("control characters are not permitted in workflow goal")
    if len(value) > MAX_WORKFLOW_GOAL_LENGTH:
        raise WorkflowValidationError(
            f"workflow goal exceeds max length: {len(value)} > {MAX_WORKFLOW_GOAL_LENGTH}"
        )
    assert_secret_free(value, "workflow goal")
    return value.strip()


def validate_description(value: Any) -> str:
    """Validate an optional bounded description."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise WorkflowValidationError("description must be a string")
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise WorkflowValidationError(
            f"description exceeds max length: {len(value)} > {MAX_DESCRIPTION_LENGTH}"
        )
    assert_secret_free(value, "description")
    return value.strip()


def assert_secret_free(value: Any, context: str = "workflow definition") -> None:
    """Fail closed when credential material appears in a workflow declaration."""
    if isinstance(value, str):
        if looks_like_secret(value):
            raise WorkflowSecurityError(
                f"credential material must not appear in {context}; use the credential broker instead"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in FORBIDDEN_PARAMETER_KEYS:
                raise WorkflowSecurityError(
                    f"credential material must not appear in {context}; use the credential broker instead"
                )
            assert_secret_free(item, context)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            assert_secret_free(item, context)


# --------------------------------------------------------------------------
# Domain, capability and operation validation
# --------------------------------------------------------------------------
def assert_domain_active(domain: CapabilityDomain | str) -> CapabilityDomain:
    """Resolve a domain and fail closed unless it is an ACTIVE capability domain."""
    try:
        resolved = coerce_domain(domain)
    except ValueError as exc:
        raise WorkflowDomainError(str(exc)) from None
    descriptor = domain_descriptor(resolved)
    if descriptor.phase is not DomainPhase.ACTIVE:
        raise WorkflowDomainError(
            f"capability domain is reserved and cannot be used in a workflow: {resolved.value}"
        )
    return resolved


def validate_capability_id(
    capability_id: Any,
    domain: CapabilityDomain | str,
    known_capability_ids: Iterable[str] | None = None,
) -> str:
    """Validate a namespaced ``<domain>:<operation>`` capability id against its domain."""
    cleaned = _clean_text(capability_id, "capability id")
    if len(cleaned) > MAX_CAPABILITY_ID_LENGTH:
        raise WorkflowValidationError(
            f"capability id exceeds max length: {len(cleaned)} > {MAX_CAPABILITY_ID_LENGTH}"
        )
    if not _CAPABILITY_ID_PATTERN.match(cleaned):
        raise WorkflowValidationError(
            f"capability id must be namespaced as <domain>:<operation>: {capability_id!r}"
        )
    resolved = assert_domain_active(domain)
    if not cleaned.startswith(f"{resolved.value}:"):
        raise WorkflowDomainError(
            f"capability id namespace must match its domain: {cleaned} is not in {resolved.value}"
        )
    if known_capability_ids is not None:
        known = {str(item) for item in known_capability_ids}
        if cleaned not in known:
            raise WorkflowDomainError(f"capability id is not registered: {cleaned}")
    return cleaned


def validate_operation(operation: Any) -> str:
    """Validate a step operation name against grammar and the forbidden list."""
    cleaned = _clean_text(operation, "operation").lower()
    if len(cleaned) > MAX_OPERATION_LENGTH:
        raise WorkflowValidationError(
            f"operation exceeds max length: {len(cleaned)} > {MAX_OPERATION_LENGTH}"
        )
    if not _OPERATION_PATTERN.match(cleaned):
        raise WorkflowValidationError(
            f"operation must be lowercase dotted snake_case: {operation!r}"
        )
    for part in cleaned.split("."):
        if part in FORBIDDEN_STEP_OPERATIONS:
            raise WorkflowSecurityError(f"operation is forbidden in a workflow step: {cleaned}")
    if cleaned in FORBIDDEN_STEP_OPERATIONS:
        raise WorkflowSecurityError(f"operation is forbidden in a workflow step: {cleaned}")
    return cleaned


def infer_effect(operation: str) -> StepEffect:
    """Infer whether an operation mutates state, from its name alone."""
    lowered = str(operation).strip().lower()
    for signal in MUTATING_OPERATION_SIGNALS:
        if signal in lowered:
            return StepEffect.MUTATING
    return StepEffect.READ_ONLY


# --------------------------------------------------------------------------
# Parameter validation
# --------------------------------------------------------------------------
def _validate_path_value(key: str, value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        raise WorkflowSecurityError(f"path parameter cannot be empty: {key}")
    if re.match(r"^[a-zA-Z]:", cleaned) or cleaned.startswith(("\\\\", "//", "/")):
        raise WorkflowSecurityError(
            f"absolute, drive-letter or UNC paths are not permitted in workflow parameters: {key}"
        )
    normalized = cleaned.replace("\\", "/")
    if ".." in normalized.split("/"):
        raise WorkflowSecurityError(f"path traversal is not permitted in workflow parameters: {key}")


def _validate_parameter_value(key: str, value: Any, depth: int = 0) -> Any:
    if depth > MAX_PARAMETER_DEPTH:
        raise WorkflowValidationError(
            f"parameter nesting exceeds max depth {MAX_PARAMETER_DEPTH}: {key}"
        )
    if isinstance(value, str):
        if len(value) > MAX_PARAMETER_VALUE_LENGTH:
            raise WorkflowValidationError(
                f"parameter value exceeds max length {MAX_PARAMETER_VALUE_LENGTH}: {key}"
            )
        if _CONTROL_CHARS.search(value):
            raise WorkflowSecurityError(f"control characters are not permitted in parameter: {key}")
        if looks_like_secret(value):
            raise WorkflowSecurityError(
                f"credential material must not appear in workflow parameters: {key}"
            )
        if key.strip().lower() in PATH_PARAMETER_KEYS:
            _validate_path_value(key, value)
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_PARAMETER_ITEMS:
            raise WorkflowValidationError(
                f"nested parameter mapping exceeds max items {MAX_PARAMETER_ITEMS}: {key}"
            )
        cleaned: dict[str, Any] = {}
        for nested_key, nested_value in value.items():
            nested_name = str(nested_key).strip()
            if not nested_name or len(nested_name) > MAX_PARAMETER_KEY_LENGTH:
                raise WorkflowValidationError(
                    f"nested parameter name is empty or exceeds max length "
                    f"{MAX_PARAMETER_KEY_LENGTH}: {key}"
                )
            if nested_name.lower() in FORBIDDEN_PARAMETER_KEYS:
                raise WorkflowSecurityError(
                    f"credential parameter is not permitted in a workflow definition: {nested_name}"
                )
            cleaned[nested_name] = _validate_parameter_value(nested_name, nested_value, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PARAMETER_ITEMS:
            raise WorkflowValidationError(
                f"parameter sequence exceeds max items {MAX_PARAMETER_ITEMS}: {key}"
            )
        return [_validate_parameter_value(key, item, depth + 1) for item in value]
    raise WorkflowValidationError(
        f"unsupported parameter type for {key}: {type(value).__name__}"
    )


def validate_parameters(parameters: Any) -> dict[str, Any]:
    """Validate bounded, secret-free, JSON-shaped step parameters."""
    if parameters is None:
        return {}
    if not isinstance(parameters, Mapping):
        raise WorkflowValidationError("step parameters must be a mapping")
    if len(parameters) > MAX_STEP_PARAMETERS:
        raise WorkflowValidationError(
            f"step parameter count exceeds max allowed: {len(parameters)} > {MAX_STEP_PARAMETERS}"
        )
    cleaned: dict[str, Any] = {}
    for key, value in parameters.items():
        name = str(key).strip()
        if not name:
            raise WorkflowValidationError("parameter names cannot be empty")
        if len(name) > MAX_PARAMETER_KEY_LENGTH:
            raise WorkflowValidationError(
                f"parameter name exceeds max length {MAX_PARAMETER_KEY_LENGTH}: {name}"
            )
        if not _PARAMETER_KEY_PATTERN.match(name):
            raise WorkflowValidationError(
                f"parameter name must be lowercase snake_case: {name!r}"
            )
        if name.lower() in FORBIDDEN_PARAMETER_KEYS:
            raise WorkflowSecurityError(
                f"credential parameter is not permitted in a workflow definition: {name}"
            )
        cleaned[name] = _validate_parameter_value(name, value)
    return cleaned


def validate_artifact_keys(values: Any, label: str) -> tuple[str, ...]:
    """Validate a bounded, duplicate-free tuple of artifact keys."""
    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise WorkflowValidationError(f"{label} must be a list or tuple of artifact keys")
    if len(values) > MAX_ARTIFACT_KEYS:
        raise WorkflowValidationError(
            f"{label} exceeds max artifact keys: {len(values)} > {MAX_ARTIFACT_KEYS}"
        )
    cleaned: list[str] = []
    for item in values:
        key = validate_artifact_key(item)
        if key in cleaned:
            raise WorkflowValidationError(f"{label} contains a duplicate artifact key: {key}")
        cleaned.append(key)
    return tuple(cleaned)


def validate_action_cost(value: Any) -> int:
    """Validate the declared action cost of a single step."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowValidationError("step action cost must be an integer")
    if value < 1:
        raise WorkflowValidationError("step action cost must be at least 1")
    if value > MAX_STEP_ACTION_COST:
        raise WorkflowValidationError(
            f"step action cost exceeds max allowed: {value} > {MAX_STEP_ACTION_COST}"
        )
    return value


def validate_action_budget(value: Any) -> int:
    """Validate a workflow-level action budget."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowValidationError("workflow action budget must be an integer")
    if value < 1:
        raise WorkflowValidationError("workflow action budget must be positive")
    if value > MAX_WORKFLOW_ACTION_BUDGET:
        raise WorkflowValidationError(
            f"workflow action budget exceeds max allowed: {value} > {MAX_WORKFLOW_ACTION_BUDGET}"
        )
    return value


def validate_metadata(metadata: Any) -> dict[str, Any]:
    """Validate bounded, secret-free workflow metadata."""
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise WorkflowValidationError("workflow metadata must be a mapping")
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise WorkflowValidationError(
            f"workflow metadata exceeds max entries: {len(metadata)} > {MAX_METADATA_ENTRIES}"
        )
    return validate_parameters(metadata)


# --------------------------------------------------------------------------
# Step validation and approval gating
# --------------------------------------------------------------------------
def domain_trust(domain: CapabilityDomain | str) -> TrustLevel:
    """Trust tier of content originating in a domain, defaulting to EXTERNAL."""
    try:
        resolved = coerce_domain(domain)
    except ValueError:
        return TrustLevel.EXTERNAL
    return DOMAIN_TRUST.get(resolved, TrustLevel.EXTERNAL)


def lowest_trust(*levels: TrustLevel) -> TrustLevel:
    """Return the most untrusted level among the arguments (taint propagation)."""
    resolved = [level for level in levels if isinstance(level, TrustLevel)]
    if not resolved:
        return TrustLevel.EXTERNAL
    return min(resolved, key=lambda level: _TRUST_RANK.get(level, 0))


def approval_reason(step: WorkflowStep, inbound_trust: TrustLevel | None = None) -> str:
    """Return why a step needs human approval, or an empty string when it does not."""
    signal = consequential_signal(step.operation, step.capability_id, step.description)
    if signal:
        return signal
    if step.mutating and step.domain in SENSITIVE_SINK_DOMAINS:
        if inbound_trust is TrustLevel.EXTERNAL:
            return (
                "mutating step in a sensitive domain consumes untrusted cross-domain content "
                f"and requires approval: {step.domain_value}:{step.operation}"
            )
        return (
            "mutating step in a sensitive domain requires approval: "
            f"{step.domain_value}:{step.operation}"
        )
    return ""


def requires_human_approval(step: WorkflowStep, inbound_trust: TrustLevel | None = None) -> bool:
    """True when policy requires an explicit human approval gate for this step."""
    return bool(approval_reason(step, inbound_trust))


def assert_approval_gate(step: WorkflowStep, inbound_trust: TrustLevel | None = None) -> None:
    """Fail closed when a step that policy requires approval for does not declare it."""
    reason = approval_reason(step, inbound_trust)
    if reason and not step.requires_approval:
        raise WorkflowApprovalError(
            f"step '{step.step_id}' must declare requires_approval: {reason}"
        )


def assert_effect_declared(step: WorkflowStep) -> None:
    """Fail closed when a mutating operation is declared read-only."""
    if infer_effect(step.operation) is StepEffect.MUTATING and not step.mutating:
        raise WorkflowSecurityError(
            f"step '{step.step_id}' understates its effect: operation "
            f"'{step.operation}' mutates state but the step is declared read-only"
        )


def validate_step(
    step: Any,
    known_capability_ids: Iterable[str] | None = None,
) -> WorkflowStep:
    """Validate one step in isolation and return a normalized copy.

    Cross-step concerns (dependency existence, cycles, handoff ordering, taint)
    are validated by :func:`validate_pipeline`.
    """
    if not isinstance(step, WorkflowStep):
        raise WorkflowValidationError("workflow steps must be WorkflowStep instances")

    step_id = validate_step_id(step.step_id)
    domain = assert_domain_active(step.domain)
    capability_id = validate_capability_id(step.capability_id, domain, known_capability_ids)
    operation = validate_operation(step.operation)

    if not isinstance(step.depends_on, (list, tuple)):
        raise WorkflowDependencyError("step depends_on must be a list or tuple")
    if len(step.depends_on) > MAX_STEP_DEPENDENCIES:
        raise WorkflowDependencyError(
            f"step '{step_id}' exceeds max dependencies: {len(step.depends_on)} > {MAX_STEP_DEPENDENCIES}"
        )
    dependencies: list[str] = []
    for item in step.depends_on:
        dependency = validate_step_id(item)
        if dependency == step_id:
            raise WorkflowDependencyError(f"step '{step_id}' cannot depend on itself")
        if dependency in dependencies:
            raise WorkflowDependencyError(
                f"step '{step_id}' declares a duplicate dependency: {dependency}"
            )
        dependencies.append(dependency)

    if not isinstance(step.effect, StepEffect):
        raise WorkflowValidationError("step effect must be a StepEffect value")
    if not isinstance(step.requires_approval, bool):
        raise WorkflowValidationError("step requires_approval must be a boolean")

    normalized = WorkflowStep(
        step_id=step_id,
        domain=domain,
        capability_id=capability_id,
        operation=operation,
        depends_on=tuple(dependencies),
        parameters=validate_parameters(step.parameters),
        consumes=validate_artifact_keys(step.consumes, f"step '{step_id}' consumes"),
        produces=validate_artifact_keys(step.produces, f"step '{step_id}' produces"),
        effect=step.effect,
        requires_approval=step.requires_approval,
        action_cost=validate_action_cost(step.action_cost),
        description=validate_description(step.description),
    )

    assert_effect_declared(normalized)
    assert_approval_gate(normalized)
    return normalized


# --------------------------------------------------------------------------
# DAG validation
# --------------------------------------------------------------------------
def validate_dependencies(steps: Sequence[WorkflowStep]) -> dict[str, tuple[str, ...]]:
    """Validate that every declared dependency exists and return the DAG edges."""
    known = [step.step_id for step in steps]
    if len(known) != len(set(known)):
        duplicates = sorted({item for item in known if known.count(item) > 1})
        raise WorkflowValidationError(
            f"workflow contains duplicate step ids: {', '.join(duplicates)}"
        )
    edges: dict[str, tuple[str, ...]] = {}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in known:
                raise WorkflowDependencyError(
                    f"step '{step.step_id}' depends on an unknown step: {dependency}"
                )
        edges[step.step_id] = tuple(step.depends_on)
    return edges


def detect_dependency_cycle(steps: Sequence[WorkflowStep]) -> tuple[str, ...]:
    """Return one cycle in the dependency graph, or an empty tuple when acyclic."""
    edges = {step.step_id: tuple(step.depends_on) for step in steps}
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> tuple[str, ...]:
        if node in done:
            return ()
        if node in visiting:
            start = stack.index(node) if node in stack else 0
            return tuple(stack[start:] + [node])
        visiting.add(node)
        stack.append(node)
        for dependency in edges.get(node, ()):  # unknown deps are handled elsewhere
            if dependency not in edges:
                continue
            found = visit(dependency)
            if found:
                return found
        stack.pop()
        visiting.discard(node)
        done.add(node)
        return ()

    for step in steps:
        cycle = visit(step.step_id)
        if cycle:
            return cycle
    return ()


def topological_order(steps: Sequence[WorkflowStep]) -> tuple[str, ...]:
    """Return a deterministic execution order, failing closed on cycles.

    Ties are broken by declaration order so the same workflow always produces
    the same plan and the same audit digest.
    """
    edges = validate_dependencies(steps)
    position = {step.step_id: index for index, step in enumerate(steps)}
    remaining = dict(edges)
    ordered: list[str] = []

    while remaining:
        ready = sorted(
            (node for node, deps in remaining.items() if all(dep in ordered for dep in deps)),
            key=lambda node: position[node],
        )
        if not ready:
            cycle = detect_dependency_cycle(steps)
            detail = " -> ".join(cycle) if cycle else ", ".join(sorted(remaining))
            raise WorkflowDependencyError(f"workflow dependency cycle detected: {detail}")
        for node in ready:
            ordered.append(node)
            remaining.pop(node, None)
    return tuple(ordered)


def transitive_dependencies(step_id: str, edges: Mapping[str, tuple[str, ...]]) -> frozenset[str]:
    """All steps that must complete before ``step_id`` may run."""
    seen: set[str] = set()
    pending = list(edges.get(step_id, ()))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(edges.get(current, ()))
    return frozenset(seen)


# --------------------------------------------------------------------------
# Cross-domain handoff policy
# --------------------------------------------------------------------------
def validate_handoff(
    handoff: Any,
    steps_by_id: Mapping[str, WorkflowStep],
    edges: Mapping[str, tuple[str, ...]],
) -> WorkflowHandoff:
    """Validate one declared handoff against the steps it connects."""
    if not isinstance(handoff, WorkflowHandoff):
        raise WorkflowHandoffError("workflow handoffs must be WorkflowHandoff instances")

    source_id = validate_step_id(handoff.source_step)
    target_id = validate_step_id(handoff.target_step)
    artifact_key = validate_artifact_key(handoff.artifact_key)

    if source_id == target_id:
        raise WorkflowHandoffError(f"handoff cannot target its own source step: {source_id}")
    source = steps_by_id.get(source_id)
    target = steps_by_id.get(target_id)
    if source is None:
        raise WorkflowHandoffError(f"handoff references an unknown source step: {source_id}")
    if target is None:
        raise WorkflowHandoffError(f"handoff references an unknown target step: {target_id}")
    if not isinstance(handoff.kind, HandoffKind):
        raise WorkflowHandoffError("handoff kind must be a HandoffKind value")
    if not isinstance(handoff.trust, TrustLevel):
        raise WorkflowHandoffError("handoff trust must be a TrustLevel value")

    if artifact_key not in source.produces:
        raise WorkflowHandoffError(
            f"source step '{source_id}' does not produce artifact '{artifact_key}'"
        )
    if artifact_key not in target.consumes:
        raise WorkflowHandoffError(
            f"target step '{target_id}' does not consume artifact '{artifact_key}'"
        )
    if source_id not in transitive_dependencies(target_id, edges):
        raise WorkflowHandoffError(
            f"target step '{target_id}' must depend on '{source_id}' to receive artifact "
            f"'{artifact_key}'"
        )

    declared_trust = domain_trust(source.domain)
    if declared_trust is TrustLevel.EXTERNAL and handoff.trust is not TrustLevel.EXTERNAL:
        raise WorkflowSecurityError(
            f"handoff from untrusted domain '{source.domain_value}' must declare EXTERNAL trust, "
            f"got '{handoff.trust.value}'"
        )

    normalized = WorkflowHandoff(
        source_step=source_id,
        target_step=target_id,
        artifact_key=artifact_key,
        kind=handoff.kind,
        trust=handoff.trust,
        description=validate_description(handoff.description),
    )
    assert_cross_domain_handoff_allowed(normalized, source, target)
    return normalized


def assert_cross_domain_handoff_allowed(
    handoff: WorkflowHandoff,
    source: WorkflowStep,
    target: WorkflowStep,
) -> None:
    """Fail closed on cross-domain transfers that policy forbids or under-gates."""
    if handoff.trust is not TrustLevel.EXTERNAL:
        return
    if target.domain in BLOCKED_UNTRUSTED_SINK_DOMAINS:
        raise WorkflowSecurityError(
            f"untrusted content from '{source.domain_value}' may never be handed to "
            f"'{target.domain_value}': shell and desktop input are hard boundaries"
        )
    if handoff.kind is HandoffKind.FILE_PATH and not target.requires_approval:
        raise WorkflowApprovalError(
            f"step '{target.step_id}' consumes an untrusted file path from "
            f"'{source.domain_value}' and must declare requires_approval"
        )
    if target.mutating and target.domain in SENSITIVE_SINK_DOMAINS and not target.requires_approval:
        raise WorkflowApprovalError(
            f"step '{target.step_id}' mutates '{target.domain_value}' using untrusted content "
            f"from '{source.domain_value}' and must declare requires_approval"
        )


def propagate_trust(
    steps: Sequence[WorkflowStep],
    edges: Mapping[str, tuple[str, ...]],
    handoffs: Sequence[WorkflowHandoff] = (),
) -> dict[str, TrustLevel]:
    """Propagate trust through the DAG: taint flows downstream and never upgrades."""
    order = topological_order(steps)
    steps_by_id = {step.step_id: step for step in steps}
    inbound_handoffs: dict[str, list[WorkflowHandoff]] = {}
    for handoff in handoffs:
        inbound_handoffs.setdefault(handoff.target_step, []).append(handoff)

    resolved: dict[str, TrustLevel] = {}
    for step_id in order:
        step = steps_by_id[step_id]
        levels = [domain_trust(step.domain)]
        for dependency in edges.get(step_id, ()):
            if dependency in resolved:
                levels.append(resolved[dependency])
        for handoff in inbound_handoffs.get(step_id, ()):
            levels.append(handoff.trust)
        resolved[step_id] = lowest_trust(*levels)
    return resolved


# --------------------------------------------------------------------------
# Budget enforcement
# --------------------------------------------------------------------------
def assert_within_action_budget(pipeline: WorkflowPipeline) -> int:
    """Fail closed when the declared step costs exceed the workflow action budget."""
    budget = validate_action_budget(pipeline.action_budget)
    total = sum(validate_action_cost(step.action_cost) for step in pipeline.steps)
    if total > budget:
        raise WorkflowSecurityError(
            f"workflow declares more actions than its budget allows: {total} > {budget}"
        )
    return total


# --------------------------------------------------------------------------
# Whole-pipeline validation
# --------------------------------------------------------------------------
def validate_pipeline(
    pipeline: Any,
    known_capability_ids: Iterable[str] | None = None,
) -> WorkflowPipeline:
    """Validate a whole cross-domain workflow and return a normalized, VALIDATED copy.

    This is the single entrypoint a planner or executor must call before a
    workflow is allowed anywhere near a domain backend.
    """
    if not isinstance(pipeline, WorkflowPipeline):
        raise WorkflowValidationError("pipeline must be a WorkflowPipeline instance")

    workflow_id = validate_workflow_id(pipeline.workflow_id)
    name = validate_workflow_name(pipeline.name)
    goal = validate_goal(pipeline.goal)
    metadata = validate_metadata(pipeline.metadata)
    budget = validate_action_budget(pipeline.action_budget)

    if not isinstance(pipeline.steps, (list, tuple)):
        raise WorkflowValidationError("workflow steps must be a list or tuple")
    if not pipeline.steps:
        raise WorkflowValidationError("workflow must declare at least one step")
    if len(pipeline.steps) > MAX_WORKFLOW_STEPS:
        raise WorkflowValidationError(
            f"workflow exceeds max steps: {len(pipeline.steps)} > {MAX_WORKFLOW_STEPS}"
        )
    if not isinstance(pipeline.handoffs, (list, tuple)):
        raise WorkflowHandoffError("workflow handoffs must be a list or tuple")
    if len(pipeline.handoffs) > MAX_WORKFLOW_HANDOFFS:
        raise WorkflowHandoffError(
            f"workflow exceeds max handoffs: {len(pipeline.handoffs)} > {MAX_WORKFLOW_HANDOFFS}"
        )

    steps = tuple(validate_step(step, known_capability_ids) for step in pipeline.steps)
    edges = validate_dependencies(steps)
    topological_order(steps)

    domains: list[CapabilityDomain] = []
    for step in steps:
        if step.domain not in domains:
            domains.append(step.domain)
    if len(domains) > MAX_WORKFLOW_DOMAINS:
        raise WorkflowValidationError(
            f"workflow spans more domains than allowed: {len(domains)} > {MAX_WORKFLOW_DOMAINS}"
        )

    steps_by_id = {step.step_id: step for step in steps}
    seen_handoffs: set[tuple[str, str, str]] = set()
    handoffs: list[WorkflowHandoff] = []
    for handoff in pipeline.handoffs:
        validated = validate_handoff(handoff, steps_by_id, edges)
        key = (validated.source_step, validated.target_step, validated.artifact_key)
        if key in seen_handoffs:
            raise WorkflowHandoffError(
                f"duplicate handoff declared: {validated.source_step} -> "
                f"{validated.target_step} ({validated.artifact_key})"
            )
        seen_handoffs.add(key)
        handoffs.append(validated)

    declared_handoffs = {(item.target_step, item.artifact_key) for item in handoffs}
    for step in steps:
        for artifact_key in step.consumes:
            if (step.step_id, artifact_key) not in declared_handoffs:
                raise WorkflowHandoffError(
                    f"step '{step.step_id}' consumes artifact '{artifact_key}' without a "
                    "declared handoff"
                )

    trust = propagate_trust(steps, edges, tuple(handoffs))
    for step in steps:
        assert_approval_gate(step, trust.get(step.step_id))

    normalized = replace(
        pipeline,
        workflow_id=workflow_id,
        name=name,
        goal=goal,
        metadata=metadata,
        steps=steps,
        handoffs=tuple(handoffs),
        action_budget=budget,
        state=WorkflowState.VALIDATED,
    )
    assert_within_action_budget(normalized)
    return normalized


def pipeline_execution_order(pipeline: WorkflowPipeline) -> tuple[str, ...]:
    """Deterministic execution order for an already-structured pipeline."""
    return topological_order(pipeline.steps)


def pipeline_trust_map(pipeline: WorkflowPipeline) -> dict[str, TrustLevel]:
    """Per-step trust after taint propagation across the whole workflow."""
    edges = validate_dependencies(pipeline.steps)
    return propagate_trust(pipeline.steps, edges, pipeline.handoffs)


__all__ = [
    "BLOCKED_UNTRUSTED_SINK_DOMAINS",
    "DOMAIN_TRUST",
    "FORBIDDEN_PARAMETER_KEYS",
    "FORBIDDEN_STEP_OPERATIONS",
    "MUTATING_OPERATION_SIGNALS",
    "PATH_PARAMETER_KEYS",
    "SENSITIVE_SINK_DOMAINS",
    "UNTRUSTED_SOURCE_DOMAINS",
    "approval_reason",
    "assert_approval_gate",
    "assert_cross_domain_handoff_allowed",
    "assert_domain_active",
    "assert_effect_declared",
    "assert_secret_free",
    "assert_within_action_budget",
    "consequential_signal",
    "detect_dependency_cycle",
    "domain_trust",
    "infer_effect",
    "lowest_trust",
    "pipeline_execution_order",
    "pipeline_trust_map",
    "propagate_trust",
    "redact_secret",
    "requires_human_approval",
    "topological_order",
    "transitive_dependencies",
    "validate_action_budget",
    "validate_action_cost",
    "validate_artifact_key",
    "validate_artifact_keys",
    "validate_capability_id",
    "validate_dependencies",
    "validate_description",
    "validate_goal",
    "validate_handoff",
    "validate_metadata",
    "validate_operation",
    "validate_parameters",
    "validate_pipeline",
    "validate_step",
    "validate_step_id",
    "validate_workflow_id",
    "validate_workflow_name",
]
