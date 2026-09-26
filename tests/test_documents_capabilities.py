"""Tests for documents capability discovery, registration, input validation, and task routing."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital.builtins import (
    BUILTIN_DECLARATIONS,
    DEFAULT_CAPABILITIES,
    DocumentsPostConditionObserver,
    build_capabilities,
)
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import (
    CapabilityAvailability,
    CapabilityExecution,
    CapabilityRequest,
)
from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.digital.provider import TOOL_SANDBOX_BINDINGS
from autonomous_agent.documents.backend import MockDocumentsBackend, _MockDocument
from autonomous_agent.documents.connector import BoundedDocumentsConnector
from autonomous_agent.documents.models import DocumentOperationType, DocumentPage, DocumentTable, DocumentType
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    REGISTRY,
    ReadWriteMode,
    RiskLevel,
)

DOCUMENTS_CAPABILITY_IDS = (
    "documents:inspect",
    "documents:extract.text",
    "documents:extract.tables",
    "documents:page.read",
    "documents:transform",
)

DOCUMENTS_TOOL_NAMES = (
    "documents.inspect",
    "documents.extract.text",
    "documents.extract.tables",
    "documents.page.read",
    "documents.transform",
)


@pytest.fixture
def mock_documents_backend(tmp_path: Path) -> MockDocumentsBackend:
    backend = MockDocumentsBackend()
    doc_path = tmp_path / "sample.pdf"
    doc_path.write_text("dummy", encoding="utf-8")
    backend.add_document(
        _MockDocument(
            relative_path="sample.pdf",
            document_type=DocumentType.PDF,
            text="Sample document text content for testing.",
            pages=(
                DocumentPage(page_number=1, text="Page 1 sample content", table_count=1),
                DocumentPage(page_number=2, text="Page 2 sample content", table_count=0),
            ),
            tables=(
                DocumentTable(table_id="tbl-1", headers=("A", "B"), rows=(("1", "2"),), page_number=1),
            ),
        )
    )
    return backend


@pytest.fixture
def documents_connector(mock_documents_backend: MockDocumentsBackend, tmp_path: Path) -> BoundedDocumentsConnector:
    return BoundedDocumentsConnector(backend=mock_documents_backend, workspace_root=tmp_path)


@pytest.fixture
def documents_catalog(documents_connector: BoundedDocumentsConnector, tmp_path: Path) -> CapabilityCatalog:
    caps = build_capabilities(
        root=tmp_path,
        connectors={"documents": documents_connector},
        tool_registry=REGISTRY,
    )
    return CapabilityCatalog(caps)


# =========================================================================
# 1. Registry and Spec Validation
# =========================================================================

def test_all_five_documents_tools_registered_in_registry():
    for tool_name in DOCUMENTS_TOOL_NAMES:
        assert REGISTRY.get(tool_name) is not None, f"Tool {tool_name} not found in REGISTRY"


def test_documents_tools_category_and_capability():
    for tool_name in DOCUMENTS_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.category == "documents"
        assert spec.capability == Capability.DOCUMENTS.value


def test_documents_tools_sandbox_and_audit_required():
    for tool_name in DOCUMENTS_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.sandbox_requirement.value == "required"
        assert spec.audit_requirement.value == "required"


def test_documents_tools_have_sandbox_bindings():
    for tool_name in DOCUMENTS_TOOL_NAMES:
        assert tool_name in TOOL_SANDBOX_BINDINGS
        op, slot, key = TOOL_SANDBOX_BINDINGS[tool_name]
        assert op == "documents"
        assert slot == "documents"
        assert key is not None


def test_all_five_documents_declarations_present():
    decl_ids = {d.capability_id for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.DOCUMENTS}
    assert decl_ids == set(DOCUMENTS_CAPABILITY_IDS)


def test_documents_default_capability_is_read_only():
    defaults = DEFAULT_CAPABILITIES.get(CapabilityDomain.DOCUMENTS)
    assert defaults == ("documents:inspect",)


def test_read_only_documents_tools_are_safe_autonomous():
    read_only_tools = ["documents.inspect", "documents.extract.text", "documents.extract.tables", "documents.page.read"]
    for tool_name in read_only_tools:
        spec = REGISTRY.get(tool_name)
        assert spec.safe_autonomous is True
        assert spec.read_write_mode is ReadWriteMode.READ_ONLY
        assert spec.approval_requirement is ApprovalRequirement.NONE
        assert spec.risk_level is RiskLevel.LOW


def test_mutating_documents_transform_is_controlled_write_and_approval_gated():
    spec = REGISTRY.get("documents.transform")
    assert spec.safe_autonomous is False
    assert spec.read_write_mode is ReadWriteMode.CONTROLLED_WRITE
    assert spec.approval_requirement is ApprovalRequirement.EXPLICIT
    assert spec.risk_level is RiskLevel.HIGH


# =========================================================================
# 2. Capability Stages and Retry Policies
# =========================================================================

def test_documents_stages_order():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.DOCUMENTS}
    assert decls["documents:inspect"].stage == 10
    assert decls["documents:extract.text"].stage == 20
    assert decls["documents:extract.tables"].stage == 20
    assert decls["documents:page.read"].stage == 20
    assert decls["documents:transform"].stage == 60


def test_read_only_documents_tools_allow_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.DOCUMENTS}
    assert decls["documents:inspect"].retry_policy.max_attempts == 2
    assert decls["documents:extract.text"].retry_policy.max_attempts == 2
    assert decls["documents:extract.tables"].retry_policy.max_attempts == 2
    assert decls["documents:page.read"].retry_policy.max_attempts == 2


def test_mutating_documents_transform_disallows_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.DOCUMENTS}
    assert decls["documents:transform"].retry_policy.max_attempts == 1


# =========================================================================
# 3. Discovery and Catalog Query Tests
# =========================================================================

def test_catalog_get_by_capability_id(documents_catalog):
    for cap_id in DOCUMENTS_CAPABILITY_IDS:
        cap = documents_catalog.get(cap_id)
        assert cap is not None, f"Failed to get {cap_id} from catalog"
        assert cap.descriptor.capability_id == cap_id


def test_catalog_by_tool_name(documents_catalog):
    for tool_name in DOCUMENTS_TOOL_NAMES:
        cap = documents_catalog.by_tool(tool_name)
        assert cap is not None, f"Failed to get {tool_name} from catalog"
        assert cap.descriptor.tool_name == tool_name


def test_catalog_discover_domain_documents(documents_catalog):
    caps = documents_catalog.discover(domain=CapabilityDomain.DOCUMENTS)
    assert len(caps) == 5
    ids = {c.capability_id for c in caps}
    assert ids == set(DOCUMENTS_CAPABILITY_IDS)


def test_catalog_discover_by_query_extract(documents_catalog):
    results = documents_catalog.discover("extract", domain=CapabilityDomain.DOCUMENTS)
    found_ids = {r.capability_id for r in results}
    assert "documents:extract.text" in found_ids
    assert "documents:extract.tables" in found_ids


def test_domain_status_with_mock_connector_reports_usable(documents_catalog):
    status = {item["domain"]: item for item in documents_catalog.domain_status()}
    assert status["documents"]["usable"] is True
    assert status["documents"]["registered_capabilities"] == 5


def test_catalog_documentation_includes_documents_domain(documents_catalog):
    docs = documents_catalog.documentation()
    domain_names = {d.domain for d in docs.domains}
    assert "documents" in domain_names
    cap_ids = {c.capability_id for c in docs.capabilities}
    for cid in DOCUMENTS_CAPABILITY_IDS:
        assert cid in cap_ids


# =========================================================================
# 4. Natural Language Routing Tests
# =========================================================================

def test_route_inspect_document(documents_catalog):
    res = documents_catalog.route("inspect document summary")
    assert "documents:inspect" in res.selected
    assert CapabilityDomain.DOCUMENTS in res.matched_domains


def test_route_read_pdf(documents_catalog):
    res = documents_catalog.route("read the pdf document")
    assert "documents:extract.text" in res.selected


def test_route_extract_tables(documents_catalog):
    res = documents_catalog.route("extract table from financial statement")
    assert "documents:extract.tables" in res.selected


def test_route_read_page(documents_catalog):
    res = documents_catalog.route("read page 2 of document")
    assert "documents:page.read" in res.selected


def test_route_merge_pdf(documents_catalog):
    res = documents_catalog.route("merge the pdf files into one")
    assert "documents:transform" in res.selected


# =========================================================================
# 5. Input Validation across all 5 capabilities
# =========================================================================

def test_validate_input_inspect_valid(documents_catalog):
    cap = documents_catalog.get("documents:inspect")
    assert cap.validate_input({"path": "sample.pdf"}).ok


def test_validate_input_inspect_missing_path(documents_catalog):
    cap = documents_catalog.get("documents:inspect")
    assert not cap.validate_input({}).ok


def test_validate_input_inspect_extra_rejected(documents_catalog):
    cap = documents_catalog.get("documents:inspect")
    assert not cap.validate_input({"path": "sample.pdf", "extra": 123}).ok


def test_validate_input_extract_text_valid(documents_catalog):
    cap = documents_catalog.get("documents:extract.text")
    assert cap.validate_input({"path": "sample.pdf", "page_range": "1-2"}).ok
    assert cap.validate_input({"path": "sample.pdf"}).ok


def test_validate_input_extract_text_missing_path(documents_catalog):
    cap = documents_catalog.get("documents:extract.text")
    assert not cap.validate_input({"page_range": "1"}).ok


def test_validate_input_extract_tables_valid(documents_catalog):
    cap = documents_catalog.get("documents:extract.tables")
    assert cap.validate_input({"path": "sample.pdf", "page_number": 1}).ok
    assert cap.validate_input({"path": "sample.pdf"}).ok


def test_validate_input_read_page_valid(documents_catalog):
    cap = documents_catalog.get("documents:page.read")
    assert cap.validate_input({"path": "sample.pdf", "page_number": 1}).ok


def test_validate_input_read_page_missing_page_number(documents_catalog):
    cap = documents_catalog.get("documents:page.read")
    assert not cap.validate_input({"path": "sample.pdf"}).ok


def test_validate_input_transform_valid(documents_catalog):
    cap = documents_catalog.get("documents:transform")
    assert cap.validate_input({
        "operation": "merge",
        "input_paths": ["doc1.pdf", "doc2.pdf"],
        "output_path": "merged.pdf",
    }).ok


def test_validate_input_transform_missing_output_path(documents_catalog):
    cap = documents_catalog.get("documents:transform")
    assert not cap.validate_input({
        "operation": "merge",
        "input_paths": ["doc1.pdf"],
    }).ok


# =========================================================================
# 6. End-to-End Sandbox Dispatch and Post-Condition Observer Tests
# =========================================================================

def test_sandbox_dispatches_inspect(documents_connector, tmp_path):
    result = run_safe_operation(
        "documents",
        tmp_path,
        documents_connector=documents_connector,
        documents_request={"operation": "inspect", "path": "sample.pdf"},
    )
    assert result.success is True
    assert "sample.pdf" in result.output


def test_sandbox_dispatches_extract_text(documents_connector, tmp_path):
    result = run_safe_operation(
        "documents",
        tmp_path,
        documents_connector=documents_connector,
        documents_request={"operation": "extract_text", "path": "sample.pdf"},
    )
    assert result.success is True
    assert "Page 1 sample content" in result.output


def test_sandbox_dispatches_extract_tables(documents_connector, tmp_path):
    result = run_safe_operation(
        "documents",
        tmp_path,
        documents_connector=documents_connector,
        documents_request={"operation": "extract_tables", "path": "sample.pdf"},
    )
    assert result.success is True
    assert "tbl-1" in result.output


def test_sandbox_dispatches_read_page(documents_connector, tmp_path):
    result = run_safe_operation(
        "documents",
        tmp_path,
        documents_connector=documents_connector,
        documents_request={"operation": "read_page", "path": "sample.pdf", "page_number": 1},
    )
    assert result.success is True
    assert "Page 1 sample content" in result.output


def test_documents_capability_execution_and_observation(documents_catalog):
    cap = documents_catalog.get("documents:inspect")
    req = CapabilityRequest("documents:inspect", "documents.inspect", {"path": "sample.pdf"})
    outcome = cap.bounded_retry(req)
    assert outcome.state == "verified"
    assert outcome.execution.success is True
    assert outcome.observation.observed is True


def test_documents_transform_requires_explicit_approval(documents_catalog):
    cap = documents_catalog.get("documents:transform")
    denied = cap.authorize(granted=[Capability.DOCUMENTS], explicitly_approved=False)
    assert denied.allowed is False
    allowed = cap.authorize(granted=[Capability.DOCUMENTS], explicitly_approved=True)
    assert allowed.allowed is True


def test_documents_read_only_tools_autonomous(documents_catalog):
    for cap_id in ("documents:inspect", "documents:extract.text", "documents:extract.tables", "documents:page.read"):
        cap = documents_catalog.get(cap_id)
        decision = cap.authorize(granted=[Capability.DOCUMENTS], explicitly_approved=False)
        assert decision.allowed is True, cap_id


def test_documents_observer_handles_execution_failure():
    observer = DocumentsPostConditionObserver(connector=None)
    req = CapabilityRequest("documents:inspect", "documents.inspect", {"path": "test.pdf"})
    exec_fail = CapabilityExecution("documents:inspect", False, error="Backend failed", boundary="documents")
    obs = observer.observe(req, exec_fail)
    assert obs.observed is False
    assert "execution failed" in obs.detail


def test_documents_observer_transform_verifies_on_match(documents_connector, tmp_path):
    observer = DocumentsPostConditionObserver(documents_connector)
    req = CapabilityRequest("documents:transform", "documents.transform", {"output_path": "sample.pdf"}, approved=True)
    meta = documents_connector.inspect("sample.pdf")
    exec_success = CapabilityExecution(
        "documents:transform",
        True,
        evidence={"output_path": "sample.pdf", "sha256": meta.sha256, "size_bytes": meta.size_bytes},
        boundary="documents",
    )
    obs = observer.observe(req, exec_success)
    assert obs.observed is True
    assert "confirmed" in obs.detail


def test_documents_observer_transform_fails_on_sha_mismatch(documents_connector, tmp_path):
    observer = DocumentsPostConditionObserver(documents_connector)
    req = CapabilityRequest("documents:transform", "documents.transform", {"output_path": "sample.pdf"}, approved=True)
    exec_mismatch = CapabilityExecution(
        "documents:transform",
        True,
        evidence={"output_path": "sample.pdf", "sha256": "wrong_sha256_hash", "size_bytes": 100},
        boundary="documents",
    )
    obs = observer.observe(req, exec_mismatch)
    assert obs.observed is False
    assert "mismatch" in obs.detail
