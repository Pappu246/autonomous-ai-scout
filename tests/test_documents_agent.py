"""Deterministic behavior, backend, session, targets, replay, and connector tests for bounded documents domain."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.documents import (
    ActionBudget,
    ActionBudgetExceededError,
    BackendUnavailableError,
    BaseDocumentsBackend,
    BoundedDocumentsConnector,
    DocumentMetadata,
    DocumentNotFoundError,
    DocumentObservation,
    DocumentOperationType,
    DocumentPage,
    DocumentReplayError,
    DocumentSecurityError,
    DocumentSessionError,
    DocumentTable,
    DocumentTarget,
    DocumentTransformError,
    DocumentTransformRequest,
    DocumentType,
    DocumentsReplayProtector,
    DocumentsSession,
    DocumentsSessionSnapshot,
    MockDocumentsBackend,
    SemanticDocumentTargetResolver,
    SessionState,
    TargetResolutionError,
    UnsupportedDocumentsBackend,
    _MockDocument,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def mock_backend() -> MockDocumentsBackend:
    backend = MockDocumentsBackend()
    doc1 = _MockDocument(
        relative_path="docs/sample.pdf",
        document_type=DocumentType.PDF,
        title="Sample PDF",
        author="Author A",
        pages=(
            DocumentPage(page_number=1, text="First page content with api_key: 123456"),
            DocumentPage(page_number=2, text="Second page analysis."),
        ),
        tables=(
            DocumentTable(table_id="tbl-1", headers=("H1", "H2"), rows=(("A", "B"),), page_number=1),
        ),
    )
    doc2 = _MockDocument(
        relative_path="docs/report.docx",
        document_type=DocumentType.DOCUMENT,
        title="Q3 Report",
        text="Quarterly report full text content.",
    )
    backend.add_document(doc1)
    backend.add_document(doc2)
    return backend


@pytest.fixture
def connector(mock_backend: MockDocumentsBackend, tmp_path: Path) -> BoundedDocumentsConnector:
    return BoundedDocumentsConnector(
        backend=mock_backend,
        workspace_root=tmp_path,
        action_budget=ActionBudget(limit=20),
    )


# --------------------------------------------------------------------------
# Base and Unsupported Backends
# --------------------------------------------------------------------------
def test_base_backend_fails_closed(tmp_path: Path):
    base = BaseDocumentsBackend()
    req = DocumentTransformRequest(DocumentOperationType.MERGE, ("a.pdf",), "out.pdf")
    with pytest.raises(BackendUnavailableError):
        base.inspect_document("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError):
        base.extract_text("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError):
        base.extract_tables("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError):
        base.read_page("a.pdf", workspace_root=tmp_path, page_number=1)
    with pytest.raises(BackendUnavailableError):
        base.transform_document(req, workspace_root=tmp_path)


def test_unsupported_backend_fails_closed(tmp_path: Path):
    unsupported = UnsupportedDocumentsBackend()
    req = DocumentTransformRequest(DocumentOperationType.MERGE, ("a.pdf",), "out.pdf")
    with pytest.raises(BackendUnavailableError, match="no safe document backend"):
        unsupported.inspect_document("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError, match="no safe document backend"):
        unsupported.extract_text("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError, match="no safe document backend"):
        unsupported.extract_tables("a.pdf", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError, match="no safe document backend"):
        unsupported.read_page("a.pdf", workspace_root=tmp_path, page_number=1)
    with pytest.raises(BackendUnavailableError, match="no safe document backend"):
        unsupported.transform_document(req, workspace_root=tmp_path)


# --------------------------------------------------------------------------
# Mock Backend Behavior
# --------------------------------------------------------------------------
def test_mock_backend_inspect(mock_backend: MockDocumentsBackend, tmp_path: Path):
    meta = mock_backend.inspect_document("docs/sample.pdf", workspace_root=tmp_path)
    assert meta.filename == "sample.pdf"
    assert meta.relative_path == "docs/sample.pdf"
    assert meta.document_type == DocumentType.PDF
    assert meta.page_count == 2
    assert meta.title == "Sample PDF"
    assert meta.author == "Author A"


def test_mock_backend_extract_text(mock_backend: MockDocumentsBackend, tmp_path: Path):
    obs = mock_backend.extract_text("docs/sample.pdf", workspace_root=tmp_path)
    assert obs.relative_path == "docs/sample.pdf"
    assert len(obs.pages) == 2
    assert "First page content" in obs.extracted_text

    # With page range
    obs_p1 = mock_backend.extract_text("docs/sample.pdf", workspace_root=tmp_path, page_range="1")
    assert len(obs_p1.pages) == 1
    assert "Second page" not in obs_p1.extracted_text


def test_mock_backend_extract_tables_and_pages(mock_backend: MockDocumentsBackend, tmp_path: Path):
    tables = mock_backend.extract_tables("docs/sample.pdf", workspace_root=tmp_path)
    assert len(tables) == 1
    assert tables[0].table_id == "tbl-1"

    page = mock_backend.read_page("docs/sample.pdf", workspace_root=tmp_path, page_number=2)
    assert page.page_number == 2
    assert "Second page" in page.text


def test_mock_backend_transform(mock_backend: MockDocumentsBackend, tmp_path: Path):
    req = DocumentTransformRequest(
        operation=DocumentOperationType.MERGE,
        input_paths=("docs/sample.pdf", "docs/report.docx"),
        output_path="merged.pdf",
    )
    result = mock_backend.transform_document(req, workspace_root=tmp_path)
    assert result.output_path == "merged.pdf"
    assert result.verified is True
    assert (tmp_path / "merged.pdf").exists()


def test_mock_backend_security_boundaries(mock_backend: MockDocumentsBackend, tmp_path: Path):
    # Macro-enabled document
    macro_doc = _MockDocument(
        relative_path="docs/macro.xlsm",
        document_type=DocumentType.SPREADSHEET,
        has_macros=True,
    )
    mock_backend.add_document(macro_doc)
    with pytest.raises(DocumentSecurityError, match="blocked"):
        mock_backend.inspect_document("docs/macro.xlsm", workspace_root=tmp_path)

    # Path traversal outside workspace
    with pytest.raises(DocumentSecurityError, match="path traversal"):
        mock_backend.inspect_document("../secret.pdf", workspace_root=tmp_path)

    # Missing document
    with pytest.raises(DocumentNotFoundError, match="not found"):
        mock_backend.inspect_document("docs/nonexistent.pdf", workspace_root=tmp_path)


# --------------------------------------------------------------------------
# Session Lifecycle and Checkpoint / Restore
# --------------------------------------------------------------------------
def test_documents_session_lifecycle(tmp_path: Path):
    session = DocumentsSession(workspace_root=tmp_path, action_budget=ActionBudget(limit=5))
    assert session.state == SessionState.OPEN
    session.note_document("docs/doc1.pdf")
    assert session.active_document == "docs/doc1.pdf"
    assert session.epoch == 1

    session.consume_action(2)
    assert session.action_budget.used == 2

    session.suspend()
    assert session.state == SessionState.SUSPENDED
    with pytest.raises(DocumentSessionError, match="not open"):
        session.consume_action(1)

    session.resume()
    assert session.state == SessionState.OPEN

    snap = session.snapshot(completed_actions=("action1",))
    assert snap.active_document == "docs/doc1.pdf"
    assert snap.action_count == 2
    assert snap.completed_actions == ("action1",)

    # Restore session
    restored = DocumentsSession.restore(snap, workspace_root=tmp_path, action_budget=session.action_budget)
    assert restored.state == SessionState.OPEN
    assert restored.active_document == "docs/doc1.pdf"
    assert restored.epoch == 1


# --------------------------------------------------------------------------
# Semantic Target Resolver
# --------------------------------------------------------------------------
def test_semantic_document_target_resolver(mock_backend: MockDocumentsBackend, tmp_path: Path):
    resolver = SemanticDocumentTargetResolver()
    obs = mock_backend.extract_text("docs/sample.pdf", workspace_root=tmp_path)

    # Resolve valid page
    tgt_page = resolver.resolve_page(obs, 1)
    assert tgt_page.target_type == "page"
    assert tgt_page.page_number == 1

    # Ensure current
    resolved_page = resolver.ensure_current(tgt_page, obs)
    assert isinstance(resolved_page, DocumentPage)
    assert resolved_page.page_number == 1

    # Resolve valid table
    tgt_tbl = resolver.resolve_table(obs, "tbl-1")
    assert tgt_tbl.target_type == "table"
    assert tgt_tbl.identifier == "tbl-1"

    resolved_table = resolver.ensure_current(tgt_tbl, obs)
    assert isinstance(resolved_table, DocumentTable)
    assert resolved_table.table_id == "tbl-1"

    # Stale target epoch
    stale_tgt = DocumentTarget(
        target_id="p1",
        relative_path="docs/sample.pdf",
        target_type="page",
        page_number=1,
        epoch=999,
    )
    with pytest.raises(TargetResolutionError, match="stale"):
        resolver.ensure_current(stale_tgt, obs)

    # Foreign target path
    foreign_tgt = DocumentTarget(
        target_id="p1",
        relative_path="docs/other.pdf",
        target_type="page",
        page_number=1,
        epoch=obs.epoch,
    )
    with pytest.raises(TargetResolutionError, match="does not match"):
        resolver.ensure_current(foreign_tgt, obs)


# --------------------------------------------------------------------------
# Replay Protection
# --------------------------------------------------------------------------
def test_documents_replay_protector():
    replay = DocumentsReplayProtector()
    key = replay.mutation_key(
        operation="merge",
        session_id="s1",
        input_paths=["a.pdf", "b.pdf"],
        output_path="out.pdf",
    )
    assert replay.already_completed(key) is False
    replay.check(key)  # passes

    replay.record(key)
    assert replay.already_completed(key) is True
    with pytest.raises(DocumentReplayError, match="refusing to repeat"):
        replay.check(key)

    replay.reset()
    assert replay.already_completed(key) is False


# --------------------------------------------------------------------------
# Connector Operations & Boundary Enforcement
# --------------------------------------------------------------------------
def test_connector_inspect_and_extract(connector: BoundedDocumentsConnector):
    meta = connector.inspect("docs/sample.pdf")
    assert meta.filename == "sample.pdf"

    obs = connector.extract_text("docs/sample.pdf")
    # Untrusted content wrapping
    assert "--- BEGIN UNTRUSTED DOCUMENT CONTENT ---" in obs.extracted_text
    assert "--- END UNTRUSTED DOCUMENT CONTENT ---" in obs.extracted_text
    # Secret redaction
    assert "123456" not in obs.extracted_text
    assert "[REDACTED]" in obs.extracted_text


def test_connector_targets_and_tables(connector: BoundedDocumentsConnector):
    tables = connector.extract_tables("docs/sample.pdf")
    assert len(tables) == 1

    page = connector.read_page("docs/sample.pdf", page_number=2)
    assert page.page_number == 2

    tgt_page = connector.resolve_target_page("docs/sample.pdf", page_number=1)
    assert tgt_page.page_number == 1

    tgt_table = connector.resolve_target_table("docs/sample.pdf", table_id="tbl-1")
    assert tgt_table.identifier == "tbl-1"


def test_connector_transform_and_replay(connector: BoundedDocumentsConnector, tmp_path: Path):
    req = DocumentTransformRequest(
        operation=DocumentOperationType.MERGE,
        input_paths=("docs/sample.pdf", "docs/report.docx"),
        output_path="merged.pdf",
    )
    res = connector.transform(req)
    assert res.verified is True

    # Replay protection blocks duplicate mutation
    with pytest.raises(DocumentReplayError, match="refusing to repeat"):
        connector.transform(req)


def test_connector_action_budget_exhaustion(mock_backend: MockDocumentsBackend, tmp_path: Path):
    conn = BoundedDocumentsConnector(
        backend=mock_backend,
        workspace_root=tmp_path,
        action_budget=ActionBudget(limit=2),
    )
    conn.inspect("docs/sample.pdf")
    conn.read_page("docs/sample.pdf", page_number=1)
    with pytest.raises(ActionBudgetExceededError, match="budget exceeded"):
        conn.extract_text("docs/sample.pdf")


def test_connector_unsupported_backend_fails_closed(tmp_path: Path):
    conn = BoundedDocumentsConnector(workspace_root=tmp_path)
    assert conn.is_live() is False
    with pytest.raises(BackendUnavailableError):
        conn.inspect("doc.pdf")
