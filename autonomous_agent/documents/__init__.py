"""Bounded document processing domain (Phase 4).

Provides bounded, safe document processing architecture, data models,
backend implementations, sessions, target resolution, replay protection,
and platform-independent security policy.

Guarantees:
- Fail-closed execution and format validation.
- Blocked executable and macro-enabled formats (.docm, .xlsm, .exe, scripts).
- Dangerous active PDF content (JavaScript, Launch actions) detected and rejected.
- Workspace path confinement and path traversal protection.
- Credential detection and redaction.
- Bounded document size, page counts, extracted text, and action budgets.
- Untrusted document content wrapping to guard against prompt injection.
- Replay protection preventing repeated mutations upon resume.
- Approval gating for consequential operations.
"""

from __future__ import annotations

from .backend import (
    BaseDocumentsBackend,
    MockDocumentsBackend,
    UnsupportedDocumentsBackend,
    _MockDocument,
    checksum,
)
from .connector import BoundedDocumentsConnector
from .models import (
    ActionBudget,
    ActionBudgetExceededError,
    BackendUnavailableError,
    DocumentError,
    DocumentExtractionError,
    DocumentFormatError,
    DocumentMetadata,
    DocumentNotFoundError,
    DocumentObservation,
    DocumentOperationType,
    DocumentPage,
    DocumentReplayError,
    DocumentSecurityError,
    DocumentSessionError,
    DocumentTable,
    DocumentTransformError,
    DocumentTransformRequest,
    DocumentTransformResult,
    DocumentType,
    MAX_ACTION_BUDGET,
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACTED_TEXT_LENGTH,
    MAX_FORM_FIELDS,
    MAX_INPUT_DOCUMENTS,
    MAX_PAGE_COUNT,
    MAX_PAGE_TEXT_LENGTH,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    REDACTED,
    TargetResolutionError,
    consequential_signal,
    looks_like_secret,
    redact_secret,
    wrap_untrusted_document_content,
)
from .policy import (
    ALLOWED_EXTENSIONS_MAP,
    BLOCKED_DOCUMENT_EXTENSIONS,
    DANGEROUS_PDF_TOKENS,
    assert_not_blocked_extension,
    assert_safe_pdf_bytes,
    bound_document_size,
    bound_extracted_text,
    bound_page_count,
    confine_document_output_path,
    confine_workspace_path,
    detect_dangerous_pdf_content,
    detect_document_type,
    sanitize_document_filename,
    validate_document_extension,
    validate_page_range,
)
from .replay import DocumentsReplayProtector
from .session import DocumentsSession, DocumentsSessionSnapshot, SessionState
from .target import DocumentTarget, SemanticDocumentTargetResolver

__all__ = [
    "ALLOWED_EXTENSIONS_MAP",
    "ActionBudget",
    "ActionBudgetExceededError",
    "BLOCKED_DOCUMENT_EXTENSIONS",
    "BackendUnavailableError",
    "BaseDocumentsBackend",
    "BoundedDocumentsConnector",
    "DANGEROUS_PDF_TOKENS",
    "DocumentError",
    "DocumentExtractionError",
    "DocumentFormatError",
    "DocumentMetadata",
    "DocumentNotFoundError",
    "DocumentObservation",
    "DocumentOperationType",
    "DocumentPage",
    "DocumentReplayError",
    "DocumentSecurityError",
    "DocumentSessionError",
    "DocumentTable",
    "DocumentTarget",
    "DocumentTransformError",
    "DocumentTransformRequest",
    "DocumentTransformResult",
    "DocumentType",
    "DocumentsReplayProtector",
    "DocumentsSession",
    "DocumentsSessionSnapshot",
    "MAX_ACTION_BUDGET",
    "MAX_DOCUMENT_BYTES",
    "MAX_EXTRACTED_TEXT_LENGTH",
    "MAX_FORM_FIELDS",
    "MAX_INPUT_DOCUMENTS",
    "MAX_PAGE_COUNT",
    "MAX_PAGE_TEXT_LENGTH",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "MockDocumentsBackend",
    "REDACTED",
    "SemanticDocumentTargetResolver",
    "SessionState",
    "TargetResolutionError",
    "UnsupportedDocumentsBackend",
    "_MockDocument",
    "assert_not_blocked_extension",
    "assert_safe_pdf_bytes",
    "bound_document_size",
    "bound_extracted_text",
    "bound_page_count",
    "checksum",
    "confine_document_output_path",
    "confine_workspace_path",
    "consequential_signal",
    "detect_dangerous_pdf_content",
    "detect_document_type",
    "looks_like_secret",
    "redact_secret",
    "sanitize_document_filename",
    "validate_document_extension",
    "validate_page_range",
    "wrap_untrusted_document_content",
]
