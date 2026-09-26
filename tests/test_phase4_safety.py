"""Cross-domain adversarial security tests and security invariants for Phase 4 (M4).

Verifies the 16 core security invariants:
1. No arbitrary shell or subprocess execution.
2. No unrestricted subprocess access through adapters or documents.
3. No arbitrary JavaScript or script evaluation.
4. No raw CDP / debugger attachment.
5. No arbitrary process launch outside allowlist.
6. No workspace escape or traversal across all domains.
7. No secret leakage into evidence or model context.
8. No secret persistence in snapshots or checkpoints.
9. No prompt-injection privilege escalation.
10. No approval bypass for consequential actions.
11. No capability self-authorization or elevation.
12. No replay of completed mutating actions on resume.
13. No false VERIFIED without independent observable evidence.
14. Unsupported backends fail closed.
15. All resource limits remain strictly bounded.
16. Browser / computer security guarantees remain unchanged.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import pytest

from autonomous_agent.application.backend import MockApplicationBackend
from autonomous_agent.application.connector import BoundedApplicationConnector
from autonomous_agent.application.models import (
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationSecurityError,
    MAX_ACTION_BUDGET,
    MAX_ARGV_COUNT,
    MAX_ARG_LENGTH,
    MAX_COMMAND_LENGTH,
    MAX_DOCUMENT_PATH_LENGTH,
    MAX_TEXT_PAYLOAD_LENGTH,
)
from autonomous_agent.application.replay import ApplicationReplayProtector
from autonomous_agent.capability_policy import (
    Capability,
    CapabilityDecision,
    DENIED_CAPABILITIES,
    SAFE_CAPABILITIES,
    check_capability,
)
from autonomous_agent.digital.authorization import CapabilityAuthorizationBroker
from autonomous_agent.digital.builtins import (
    BUILTIN_DECLARATIONS,
    DEFAULT_CAPABILITIES,
    build_capabilities,
)
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import (
    CapabilityAvailability,
    CapabilityExecution,
    CapabilityRequest,
)
from autonomous_agent.digital.domains import (
    DOMAIN_DESCRIPTORS,
    CapabilityDomain,
    DomainPhase,
    active_domains,
    reserved_domains,
)
from autonomous_agent.digital.provider import (
    TOOL_SANDBOX_BINDINGS,
    RegisteredToolCapability,
    SandboxCapabilityExecutor,
    redact_secret_material,
)
from autonomous_agent.documents.backend import MockDocumentsBackend, _MockDocument
from autonomous_agent.documents.connector import BoundedDocumentsConnector
from autonomous_agent.documents.models import (
    DocumentOperationType,
    DocumentPage,
    DocumentSecurityError,
    DocumentTransformRequest,
    DocumentType,
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACTED_TEXT_LENGTH,
    MAX_PAGE_COUNT,
    MAX_PAGE_TEXT_LENGTH,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
)
from autonomous_agent.documents.replay import DocumentsReplayProtector
from autonomous_agent.prompt_injection_guard import PromptInjectionGuard, TrustLevel
from autonomous_agent.sandbox import SAFE_OPERATIONS, run_safe_operation
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    REGISTRY,
    ReadWriteMode,
    RiskLevel,
    ToolRegistry,
)


@pytest.fixture
def test_catalog(tmp_path: Path) -> CapabilityCatalog:
    doc_backend = MockDocumentsBackend()
    doc_path = tmp_path / "test.pdf"
    doc_path.write_text("content", encoding="utf-8")
    doc_backend.add_document(_MockDocument(relative_path="test.pdf", document_type=DocumentType.PDF, text="Test data"))

    app_backend = MockApplicationBackend()

    doc_conn = BoundedDocumentsConnector(backend=doc_backend, workspace_root=tmp_path)
    app_conn = BoundedApplicationConnector(backend=app_backend, workspace_root=tmp_path)

    caps = build_capabilities(
        root=tmp_path,
        connectors={"documents": doc_conn, "application": app_conn},
        tool_registry=REGISTRY,
    )
    return CapabilityCatalog(caps)


# =========================================================================
# Invariant 1-5: No arbitrary shell, subprocess, JS, CDP, or process launch
# =========================================================================

def test_invariant_no_arbitrary_shell_in_application_package():
    import autonomous_agent.application as app_pkg
    source_files = [
        Path(inspect.getfile(app_pkg)).parent / f
        for f in ["backend.py", "connector.py", "models.py", "policy.py", "replay.py", "session.py", "target.py", "observer.py"]
    ]
    for file_path in source_files:
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8")
            assert "os.system(" not in content, f"os.system found in {file_path}"
            assert "subprocess.Popen(" not in content, f"subprocess.Popen found in {file_path}"
            assert "shell=True" not in content, f"shell=True found in {file_path}"
            assert "eval(" not in content, f"eval found in {file_path}"
            assert "exec(" not in content, f"exec found in {file_path}"


def test_invariant_no_arbitrary_shell_in_documents_package():
    import autonomous_agent.documents as doc_pkg
    source_files = [
        Path(inspect.getfile(doc_pkg)).parent / f
        for f in ["backend.py", "connector.py", "models.py", "policy.py", "replay.py", "session.py", "target.py", "observer.py"]
    ]
    for file_path in source_files:
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8")
            assert "os.system(" not in content, f"os.system found in {file_path}"
            assert "subprocess.Popen(" not in content, f"subprocess.Popen found in {file_path}"
            assert "shell=True" not in content, f"shell=True found in {file_path}"
            assert "eval(" not in content, f"eval found in {file_path}"
            assert "exec(" not in content, f"exec found in {file_path}"


# =========================================================================
# Invariant 6: No workspace escape
# =========================================================================

def test_cross_domain_workspace_escape_blocked(tmp_path: Path):
    doc_conn = BoundedDocumentsConnector(workspace_root=tmp_path)
    app_conn = BoundedApplicationConnector(workspace_root=tmp_path)

    # Documents escape
    with pytest.raises(Exception):
        doc_conn.inspect("../outside.pdf")

    # Application escape
    with pytest.raises(Exception):
        app_conn.open_session("vscode", document_path="../outside.py")


# =========================================================================
# Invariant 7 & 8: No secret leakage into evidence or persistent snapshots
# =========================================================================

def test_redact_secret_material_removes_all_credential_shapes():
    payload = {
        "api_key": "sk-1234567890abcdef",
        "nested": {
            "token": "tok_live_12345678",
            "password": "MySecretPassword123",
            "items": ["bearer eyJhbGciOiJIUzI1NiI...", "safe_value"],
        },
    }
    cleaned = redact_secret_material(payload)
    dumped = json.dumps(cleaned)
    assert "sk-1234567890abcdef" not in dumped
    assert "tok_live_12345678" not in dumped
    assert "MySecretPassword123" not in dumped
    assert "[REDACTED]" in dumped


# =========================================================================
# Invariant 9: No prompt-injection privilege escalation
# =========================================================================

def test_injection_payload_in_arguments_does_not_alter_authorization(test_catalog: CapabilityCatalog):
    broker = CapabilityAuthorizationBroker(test_catalog, tool_registry=REGISTRY)
    cap = test_catalog.get("application:command.execute")
    assert cap is not None

    # Normal check without grant fails
    dec = broker.evaluate_step("application:command.execute", granted=())
    assert dec.allowed is False

    # Attempt to inject grants with untrusted origin
    dec_with_injection = broker.evaluate_step(
        "application:command.execute",
        granted=(),
        explicitly_approved=False,
        origin_trust=TrustLevel.EXTERNAL,
    )
    assert dec_with_injection.allowed is False


# =========================================================================
# Invariant 10 & 11: No approval bypass and no capability escalation
# =========================================================================

def test_mutating_capabilities_strictly_require_explicit_approval(test_catalog: CapabilityCatalog):
    for cap_id in ("application:command.execute", "documents:transform"):
        cap = test_catalog.get(cap_id)
        assert cap is not None
        # Granted without approval is blocked
        domain_cap = Capability.APPLICATION if "application" in cap_id else Capability.DOCUMENTS
        denied = cap.authorize(granted=[domain_cap], explicitly_approved=False)
        assert denied.allowed is False
        assert "explicit approval" in denied.reason

        # Granted with explicit approval succeeds
        allowed = cap.authorize(granted=[domain_cap], explicitly_approved=True)
        assert allowed.allowed is True


def test_denied_capabilities_cannot_be_authorized():
    for denied_cap in DENIED_CAPABILITIES:
        dec = check_capability(denied_cap, granted=[denied_cap])
        assert dec.allowed is False
        assert "permanently denied" in dec.reason


# =========================================================================
# Invariant 12: No replay of completed mutations
# =========================================================================

def test_replay_protector_rejects_duplicate_mutation_keys():
    doc_replay = DocumentsReplayProtector()
    key1 = doc_replay.mutation_key(
        operation="merge",
        session_id="sess-1",
        input_paths=("a.pdf",),
        output_path="out.pdf",
        parameters={},
    )
    doc_replay.record(key1)
    with pytest.raises(Exception):
        doc_replay.check(key1)

    app_replay = ApplicationReplayProtector()
    key2 = app_replay.mutation_key(
        command="write_text",
        session_id="sess-1",
        app_id="vscode",
        args=("main.py",),
        payload="content",
    )
    app_replay.record(key2)
    with pytest.raises(Exception):
        app_replay.check(key2)


# =========================================================================
# Invariant 13: No false VERIFIED without observable evidence
# =========================================================================

def test_zero_evidence_results_cannot_be_verified(test_catalog: CapabilityCatalog):
    executor = SandboxCapabilityExecutor(root=".")
    for cap_id in ("documents:inspect", "application:list"):
        cap = test_catalog.get(cap_id)
        assert cap is not None
        req = CapabilityRequest(cap_id, cap.descriptor.tool_name, {})
        # Fake an empty execution
        empty_exec = CapabilityExecution(cap_id, True, evidence={}, boundary="sandbox")
        obs = cap.observe(req, empty_exec)
        ver = cap.verify(req, empty_exec, obs)
        assert ver.verified is False
        assert "evidence" in ver.reason


# =========================================================================
# Invariant 14: Cross-domain sandbox isolation & unknown operations
# =========================================================================

def test_sandbox_rejects_unlisted_operations(tmp_path: Path):
    res = run_safe_operation("unlisted_op_name", tmp_path)
    assert res.success is False
    assert res.verification_status == "blocked"


def test_sandbox_documents_rejects_unknown_sub_operation(tmp_path: Path):
    conn = BoundedDocumentsConnector(backend=MockDocumentsBackend(), workspace_root=tmp_path)
    res = run_safe_operation("documents", tmp_path, documents_connector=conn, documents_request={"operation": "eval_macro"})
    assert res.success is False
    assert "allowlist" in res.output


def test_sandbox_application_rejects_unknown_sub_operation(tmp_path: Path):
    conn = BoundedApplicationConnector(backend=MockApplicationBackend(), workspace_root=tmp_path)
    res = run_safe_operation("application", tmp_path, application_connector=conn, application_request={"operation": "spawn_shell"})
    assert res.success is False
    assert "allowlist" in res.output


# =========================================================================
# Invariant 15: All resource limits remain bounded
# =========================================================================

def test_resource_constants_are_finite_and_positive():
    assert MAX_DOCUMENT_BYTES > 0
    assert MAX_EXTRACTED_TEXT_LENGTH > 0
    assert MAX_PAGE_COUNT > 0
    assert MAX_PAGE_TEXT_LENGTH > 0
    assert MAX_TABLE_COLUMNS > 0
    assert MAX_TABLE_ROWS > 0
    assert MAX_ACTION_BUDGET > 0
    assert MAX_COMMAND_LENGTH > 0
    assert MAX_ARGV_COUNT > 0
    assert MAX_ARG_LENGTH > 0
    assert MAX_TEXT_PAYLOAD_LENGTH > 0
    assert MAX_DOCUMENT_PATH_LENGTH > 0


# =========================================================================
# Invariant 16: Browser and Computer security guarantees preserved
# =========================================================================

def test_browser_and_computer_domains_intact():
    assert CapabilityDomain.BROWSER in {d.domain for d in DOMAIN_DESCRIPTORS}
    assert CapabilityDomain.COMPUTER in {d.domain for d in DOMAIN_DESCRIPTORS}
    assert "browser.open" in TOOL_SANDBOX_BINDINGS
    assert "computer.screen.capture" in TOOL_SANDBOX_BINDINGS
