"""Phase 6 M5 security review tests.

The review is intentionally static and contract-focused: Phase 6 must not grow
an implicit shell/process/browser/network escape hatch while real execution is
being enabled.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.filesystem_workspace import WorkspaceConnector
from autonomous_agent.workflow import (
    ControlledRealWorkflowBackend,
    StepEffect,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowStep,
    WorkspaceRealWorkflowAdapter,
    WorkspaceRealWorkflowWriteAdapter,
)
from autonomous_agent.workflow.real_backend import (
    RealBackendContractError,
    RealNetworkPolicy,
)


PHASE6_SOURCE_FILES = (
    Path("autonomous_agent/workflow/real_backend.py"),
    Path("autonomous_agent/workflow/real_adapters.py"),
)

DISALLOWED_IMPORT_ROOTS = {
    "socket",
    "subprocess",
    "smtplib",
    "requests",
    "httpx",
    "urllib3",
    "webbrowser",
    "selenium",
    "playwright",
    "pyautogui",
}

DISALLOWED_CALL_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
}

DISALLOWED_ATTRIBUTE_CALLS = {
    "system",
    "popen",
    "Popen",
    "spawn",
    "fork",
    "run",
    "check_call",
    "check_output",
}


def _walk_phase6_ast():
    for path in PHASE6_SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        yield path, tree


def test_m5_static_review_has_no_escape_hatch_imports_or_calls():
    violations: list[str] = []
    for path, tree in _walk_phase6_ast():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in DISALLOWED_IMPORT_ROOTS:
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in DISALLOWED_IMPORT_ROOTS:
                    violations.append(f"{path}: from {node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in DISALLOWED_CALL_NAMES:
                    violations.append(f"{path}: call {node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in DISALLOWED_ATTRIBUTE_CALLS:
                    violations.append(f"{path}: call .{node.func.attr}")
    assert not violations, "\n".join(violations)


def test_m5_workspace_real_adapters_are_network_none(tmp_path: Path):
    workspace = WorkspaceConnector(tmp_path)
    read = WorkspaceRealWorkflowAdapter(workspace).describe("filesystem:read", "read")
    write = WorkspaceRealWorkflowWriteAdapter(workspace).describe("filesystem:write", "write")
    assert read.network_policy is RealNetworkPolicy.NONE
    assert write.network_policy is RealNetworkPolicy.NONE
    assert read.effect is StepEffect.READ_ONLY
    assert write.effect is StepEffect.MUTATING
    assert read.requires_approval is False
    assert write.requires_approval is True
    assert read.observable and read.idempotent
    assert write.observable and write.idempotent


def test_m5_default_real_backend_fails_closed():
    backend = ControlledRealWorkflowBackend()
    assert backend.is_live() is False
    with pytest.raises(Exception) as error:
        backend._require_adapter()
    assert "failing closed" in str(error.value).lower()


def test_m5_mutating_descriptor_cannot_bypass_approval():
    with pytest.raises(WorkflowSecurityError):
        from autonomous_agent.workflow.real_backend import ProviderOperationDescriptor
        ProviderOperationDescriptor(
            capability_id="filesystem:write",
            operation="write",
            provider="test",
            network_policy=RealNetworkPolicy.NONE,
            required_scopes=("workspace.write",),
            effect=StepEffect.MUTATING,
            requires_approval=False,
        )


def test_m5_real_operation_must_be_observable_and_idempotent():
    from autonomous_agent.workflow.real_backend import ProviderOperationDescriptor

    with pytest.raises(RealBackendContractError):
        ProviderOperationDescriptor(
            capability_id="filesystem:read",
            operation="read",
            provider="test",
            network_policy=RealNetworkPolicy.NONE,
            effect=StepEffect.READ_ONLY,
            requires_approval=False,
            observable=False,
            idempotent=True,
        )

    with pytest.raises(RealBackendContractError):
        ProviderOperationDescriptor(
            capability_id="filesystem:read",
            operation="read",
            provider="test",
            network_policy=RealNetworkPolicy.NONE,
            effect=StepEffect.READ_ONLY,
            requires_approval=False,
            observable=True,
            idempotent=False,
        )


def test_m5_delivery_operations_remain_blocked():
    from autonomous_agent.workflow.real_backend import ProviderOperationDescriptor

    with pytest.raises(WorkflowSecurityError):
        ProviderOperationDescriptor(
            capability_id="communication:send",
            operation="send",
            provider="test",
            network_policy=RealNetworkPolicy.NONE,
            effect=StepEffect.MUTATING,
            requires_approval=True,
            observable=True,
            idempotent=True,
        )


def test_m5_controlled_real_backend_does_not_accept_unregistered_capability(tmp_path: Path):
    workspace = WorkspaceConnector(tmp_path)
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowAdapter(workspace))
    pipeline = WorkflowPipeline(
        workflow_id="wf-m5-capability",
        name="M5 capability boundary",
        steps=(
            WorkflowStep(
                step_id="read",
                domain=CapabilityDomain.FILESYSTEM,
                capability_id="filesystem:delete",
                operation="delete",
                effect=StepEffect.MUTATING,
                requires_approval=True,
            ),
        ),
        action_budget=3,
    )
    with pytest.raises(Exception):
        from autonomous_agent.workflow import BoundedWorkflowConnector
        BoundedWorkflowConnector(
            backend=backend,
            known_capability_ids=("filesystem:read",),
        ).validate(pipeline)
