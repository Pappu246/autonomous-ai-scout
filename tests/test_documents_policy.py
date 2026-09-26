"""Tests for document processing models, bounds, security policy, and active content detection."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.documents import (
    ALLOWED_EXTENSIONS_MAP,
    BLOCKED_DOCUMENT_EXTENSIONS,
    DANGEROUS_PDF_TOKENS,
    MAX_ACTION_BUDGET,
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACTED_TEXT_LENGTH,
    MAX_PAGE_COUNT,
    REDACTED,
    ActionBudget,
    ActionBudgetExceededError,
    DocumentError,
    DocumentExtractionError,
    DocumentFormatError,
    DocumentMetadata,
    DocumentNotFoundError,
    DocumentObservation,
    DocumentOperationType,
    DocumentPage,
    DocumentSecurityError,
    DocumentTable,
    DocumentTransformError,
    DocumentTransformRequest,
    DocumentTransformResult,
    DocumentType,
    assert_not_blocked_extension,
    assert_safe_pdf_bytes,
    bound_document_size,
    bound_extracted_text,
    bound_page_count,
    confine_document_output_path,
    confine_workspace_path,
    consequential_signal,
    detect_dangerous_pdf_content,
    detect_document_type,
    looks_like_secret,
    redact_secret,
    sanitize_document_filename,
    validate_document_extension,
    validate_page_range,
    wrap_untrusted_document_content,
)


# --------------------------------------------------------------------------
# Exceptions and Hierarchy
# --------------------------------------------------------------------------
def test_exception_hierarchy():
    assert issubclass(DocumentError, Exception)
    assert issubclass(DocumentSecurityError, DocumentError)
    assert issubclass(DocumentFormatError, DocumentError)
    assert issubclass(DocumentNotFoundError, DocumentError)
    assert issubclass(DocumentExtractionError, DocumentError)
    assert issubclass(DocumentTransformError, DocumentError)
    assert issubclass(ActionBudgetExceededError, DocumentError)


# --------------------------------------------------------------------------
# ActionBudget
# --------------------------------------------------------------------------
def test_action_budget_lifecycle():
    budget = ActionBudget(limit=10)
    assert budget.limit == 10
    assert budget.used == 0
    assert budget.remaining == 10

    budget.consume(4)
    assert budget.used == 4
    assert budget.remaining == 6

    budget.consume(6)
    assert budget.used == 10
    assert budget.remaining == 0

    with pytest.raises(ActionBudgetExceededError, match="budget exceeded"):
        budget.consume(1)

    budget.reset()
    assert budget.used == 0
    assert budget.remaining == 10


def test_action_budget_invalid():
    with pytest.raises(ValueError, match="limit must be positive"):
        ActionBudget(limit=0)
    budget = ActionBudget(limit=5)
    with pytest.raises(ValueError, match="count must be positive"):
        budget.consume(-2)


# --------------------------------------------------------------------------
# Redaction, Secrets, and Untrusted Content Wrapping
# --------------------------------------------------------------------------
def test_secret_detection_and_redaction():
    assert looks_like_secret("api_key = abcdef123456") is True
    assert looks_like_secret("Bearer token12345") is True
    assert looks_like_secret("password: secretpassword") is True
    assert looks_like_secret("Standard document paragraph.") is False

    text = "Report generated with authorization: Bearer key999 and token = tok123"
    redacted = redact_secret(text)
    assert "key999" not in redacted
    assert "tok123" not in redacted
    assert REDACTED in redacted


def test_wrap_untrusted_document_content():
    content = "Summary of Q3 results with password: pass123"
    wrapped = wrap_untrusted_document_content(content)
    assert "--- BEGIN UNTRUSTED DOCUMENT CONTENT ---" in wrapped
    assert "--- END UNTRUSTED DOCUMENT CONTENT ---" in wrapped
    assert "pass123" not in wrapped
    assert REDACTED in wrapped
    assert wrap_untrusted_document_content(None) == ""


# --------------------------------------------------------------------------
# Consequential Signals
# --------------------------------------------------------------------------
def test_consequential_signal():
    assert consequential_signal("Sign document with digital signature") != ""
    assert consequential_signal("Decrypt PDF using password") != ""
    assert consequential_signal("Execute macro embedded in sheet") != ""
    assert consequential_signal("Delete all pages and wipe document") != ""
    assert consequential_signal("Perform destructive edit on original") != ""
    assert consequential_signal("Extract text from first page") == ""
    assert consequential_signal("Read table headers") == ""


# --------------------------------------------------------------------------
# Data Models and Serialization
# --------------------------------------------------------------------------
def test_document_metadata():
    meta = DocumentMetadata(
        filename="report.pdf",
        relative_path="docs/report.pdf",
        document_type=DocumentType.PDF,
        size_bytes=10240,
        sha256="abc123sha",
        page_count=5,
        title="Annual Report",
        author="Acme Corp",
        is_encrypted=False,
        has_macros=False,
    )
    data = meta.safe_dict()
    assert data["filename"] == "report.pdf"
    assert data["relative_path"] == "docs/report.pdf"
    assert data["document_type"] == "pdf"
    assert data["size_bytes"] == 10240
    assert data["sha256"] == "abc123sha"
    assert data["page_count"] == 5
    assert data["title"] == "Annual Report"
    assert data["author"] == "Acme Corp"
    assert data["is_encrypted"] is False


def test_document_page():
    page = DocumentPage(
        page_number=1,
        text="Page content here",
        table_count=1,
        image_count=2,
        form_fields=("name_field", "date_field"),
    )
    data = page.safe_dict()
    assert data["page_number"] == 1
    assert data["text"] == "Page content here"
    assert data["table_count"] == 1
    assert data["image_count"] == 2
    assert data["form_fields"] == ("name_field", "date_field")


def test_document_table():
    table = DocumentTable(
        table_id="tbl-1",
        headers=("Col A", "Col B"),
        rows=(("1", "2"), ("3", "4")),
        page_number=2,
    )
    data = table.safe_dict()
    assert data["table_id"] == "tbl-1"
    assert data["headers"] == ("Col A", "Col B")
    assert data["rows"] == [["1", "2"], ["3", "4"]]
    assert data["page_number"] == 2


def test_document_observation():
    meta = DocumentMetadata("sheet.xlsx", "finances/sheet.xlsx", DocumentType.SPREADSHEET, 2048)
    obs = DocumentObservation(
        relative_path="finances/sheet.xlsx",
        document_type=DocumentType.SPREADSHEET,
        metadata=meta,
        extracted_text="Revenue: $50k",
        pages=(),
        tables=(),
        warnings=("Truncated rows",),
        epoch=1,
    )
    data = obs.safe_dict()
    assert data["relative_path"] == "finances/sheet.xlsx"
    assert data["document_type"] == "spreadsheet"
    assert data["metadata"]["filename"] == "sheet.xlsx"
    assert data["extracted_text"] == "Revenue: $50k"
    assert data["warnings"] == ["Truncated rows"]
    assert data["epoch"] == 1


def test_document_transform_request_and_result():
    req = DocumentTransformRequest(
        operation=DocumentOperationType.MERGE,
        input_paths=("doc1.pdf", "doc2.pdf"),
        output_path="merged.pdf",
    )
    req_data = req.safe_dict()
    assert req_data["operation"] == "merge"
    assert req_data["input_paths"] == ["doc1.pdf", "doc2.pdf"]
    assert req_data["output_path"] == "merged.pdf"

    res = DocumentTransformResult(
        operation=DocumentOperationType.MERGE,
        output_path="merged.pdf",
        size_bytes=20480,
        sha256="deadbeef",
        page_count=10,
        verified=True,
    )
    res_data = res.safe_dict()
    assert res_data["operation"] == "merge"
    assert res_data["output_path"] == "merged.pdf"
    assert res_data["size_bytes"] == 20480
    assert res_data["sha256"] == "deadbeef"
    assert res_data["page_count"] == 10
    assert res_data["verified"] is True


# --------------------------------------------------------------------------
# Format Detection and Extension Policy
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path, expected_type",
    [
        ("report.pdf", DocumentType.PDF),
        ("docs/memo.docx", DocumentType.DOCUMENT),
        ("notes.txt", DocumentType.TEXT),
        ("README.md", DocumentType.TEXT),
        ("data.csv", DocumentType.CSV),
        ("table.tsv", DocumentType.CSV),
        ("sheet.xlsx", DocumentType.SPREADSHEET),
        ("presentation.pptx", DocumentType.PRESENTATION),
        ("slides.odp", DocumentType.PRESENTATION),
    ],
)
def test_detect_document_type(path: str, expected_type: DocumentType):
    assert detect_document_type(path) == expected_type
    assert validate_document_extension(path) == Path(path).suffix.lower()


@pytest.mark.parametrize(
    "macro_ext",
    [
        "sheet.xlsm",
        "doc.docm",
        "pres.pptm",
        "file.xltm",
        "payload.exe",
        "script.bat",
        "command.cmd",
        "script.ps1",
        "script.vbs",
        "app.jar",
        "code.py",
        "shell.sh",
    ],
)
def test_blocked_extensions_rejected(macro_ext: str):
    with pytest.raises(DocumentSecurityError, match="blocked"):
        detect_document_type(macro_ext)
    with pytest.raises(DocumentSecurityError, match="blocked"):
        assert_not_blocked_extension(macro_ext)


def test_invalid_document_extensions():
    with pytest.raises(DocumentFormatError, match="non-empty string"):
        detect_document_type("")
    with pytest.raises(DocumentFormatError, match="unsupported document format"):
        detect_document_type("file.unknownext")


# --------------------------------------------------------------------------
# Bounds Checking
# --------------------------------------------------------------------------
def test_bound_document_size():
    assert bound_document_size(1024) == 1024
    assert bound_document_size(MAX_DOCUMENT_BYTES) == MAX_DOCUMENT_BYTES

    with pytest.raises(DocumentSecurityError, match="cannot be negative"):
        bound_document_size(-1)
    with pytest.raises(DocumentSecurityError, match="exceeds the maximum bound"):
        bound_document_size(MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(DocumentSecurityError, match="must be an integer"):
        bound_document_size("invalid")


def test_bound_page_count():
    assert bound_page_count(50) == 50
    assert bound_page_count(MAX_PAGE_COUNT) == MAX_PAGE_COUNT

    with pytest.raises(DocumentSecurityError, match="cannot be negative"):
        bound_page_count(-1)
    with pytest.raises(DocumentSecurityError, match="exceeds maximum bound"):
        bound_page_count(MAX_PAGE_COUNT + 1)


def test_bound_extracted_text():
    short_text = "This is short text."
    assert bound_extracted_text(short_text) == short_text

    long_text = "a" * (MAX_EXTRACTED_TEXT_LENGTH + 500)
    clamped = bound_extracted_text(long_text)
    assert len(clamped) == MAX_EXTRACTED_TEXT_LENGTH

    with pytest.raises(DocumentSecurityError, match="must be a string"):
        bound_extracted_text(123)


# --------------------------------------------------------------------------
# Page Range Validation
# --------------------------------------------------------------------------
def test_validate_page_range_string():
    assert validate_page_range("1-3,5", total_pages=10) == (1, 2, 3, 5)
    assert validate_page_range("1,2,3", total_pages=5) == (1, 2, 3)
    assert validate_page_range("4", total_pages=10) == (4,)
    assert validate_page_range("1-2, 2-3", total_pages=5) == (1, 2, 3)

    with pytest.raises(DocumentFormatError, match="cannot be empty"):
        validate_page_range("", total_pages=5)
    with pytest.raises(DocumentFormatError, match="invalid page range format"):
        validate_page_range("1-a", total_pages=5)
    with pytest.raises(DocumentFormatError, match="invalid page range values"):
        validate_page_range("5-2", total_pages=5)
    with pytest.raises(DocumentFormatError, match="exceeds total pages"):
        validate_page_range("6", total_pages=5)
    with pytest.raises(DocumentFormatError, match="must be >= 1"):
        validate_page_range("0", total_pages=5)


def test_validate_page_range_sequence():
    assert validate_page_range([1, 2, 4], total_pages=10) == (1, 2, 4)
    assert validate_page_range((1, 3, 5), total_pages=5) == (1, 3, 5)

    with pytest.raises(DocumentFormatError, match="page item must be an integer"):
        validate_page_range(["a", "b"], total_pages=5)
    with pytest.raises(DocumentFormatError, match="must be >= 1"):
        validate_page_range([0, 1], total_pages=5)
    with pytest.raises(DocumentFormatError, match="exceeds total pages"):
        validate_page_range([1, 6], total_pages=5)


# --------------------------------------------------------------------------
# Path Confinement and Filename Sanitization
# --------------------------------------------------------------------------
def test_sanitize_document_filename():
    assert sanitize_document_filename("report.pdf") == "report.pdf"
    assert sanitize_document_filename("../../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_document_filename("evil;rm -rf.docx") == "evil_rm_-rf.docx"
    assert sanitize_document_filename("") == "document.pdf"
    assert sanitize_document_filename("..") == "document.pdf"

    with pytest.raises(DocumentSecurityError, match="must be a string"):
        sanitize_document_filename(123)


def test_confine_workspace_path(tmp_path: Path):
    dest, norm = confine_workspace_path(tmp_path, "reports/quarterly/q3.pdf")
    assert dest == (tmp_path / "reports/quarterly/q3.pdf").resolve()
    assert norm == "reports/quarterly/q3.pdf"

    with pytest.raises(DocumentSecurityError, match="workspace-relative path is required"):
        confine_workspace_path(tmp_path, "")
    with pytest.raises(DocumentSecurityError, match="path traversal is not permitted"):
        confine_workspace_path(tmp_path, "../../secret.pdf")
    with pytest.raises(DocumentSecurityError, match="path traversal is not permitted"):
        confine_workspace_path(tmp_path, "/etc/shadow.pdf")


def test_confine_document_output_path(tmp_path: Path):
    dest, norm = confine_document_output_path(tmp_path, "../../../evil.pdf")
    assert dest == (tmp_path / "evil.pdf").resolve()
    assert norm == "evil.pdf"


# --------------------------------------------------------------------------
# Dangerous PDF Active Content Detection
# --------------------------------------------------------------------------
def test_detect_dangerous_pdf_content():
    clean_pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    assert detect_dangerous_pdf_content(clean_pdf) == ()
    assert_safe_pdf_bytes(clean_pdf)

    js_pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Action /S /JavaScript /JS (app.alert(1);) >>\nendobj\n"
    threats = detect_dangerous_pdf_content(js_pdf)
    assert "/JavaScript" in threats
    assert "/JS" in threats
    assert "/Action" in threats

    with pytest.raises(DocumentSecurityError, match="PDF contains forbidden active"):
        assert_safe_pdf_bytes(js_pdf)

    launch_pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Action /S /Launch /F (cmd.exe) >>\nendobj\n"
    assert "/Launch" in detect_dangerous_pdf_content(launch_pdf)
    with pytest.raises(DocumentSecurityError, match="PDF contains forbidden active"):
        assert_safe_pdf_bytes(launch_pdf)
