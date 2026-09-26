"""Document processing backends for the bounded documents domain.

A backend is the structured surface for inspecting, extracting, and transforming
documents.

- :class:`BaseDocumentsBackend` defines the safe, structured surface.
- :class:`MockDocumentsBackend` is a deterministic in-memory and workspace-confined
  backend for CI and tests.
- :class:`UnsupportedDocumentsBackend` is the default when no safe backend is
  configured; every operation fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    BackendUnavailableError,
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
    MAX_EXTRACTED_TEXT_LENGTH,
)
from .policy import (
    assert_not_blocked_extension,
    assert_safe_pdf_bytes,
    bound_document_size,
    bound_extracted_text,
    confine_document_output_path,
    confine_workspace_path,
    detect_document_type,
    validate_page_range,
)


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _MockDocument:
    """An in-memory document model for the deterministic backend."""

    relative_path: str
    document_type: DocumentType = DocumentType.DOCUMENT
    title: str = ""
    author: str = ""
    is_encrypted: bool = False
    has_macros: bool = False
    pages: tuple[DocumentPage, ...] = ()
    tables: tuple[DocumentTable, ...] = ()
    raw_bytes: bytes = b""
    text: str = ""


class BaseDocumentsBackend:
    """The safe, structured document processing surface."""

    name = "base"

    def inspect_document(self, path: str, *, workspace_root: Path | str) -> DocumentMetadata:
        raise BackendUnavailableError("base backend cannot inspect documents")

    def extract_text(
        self,
        path: str,
        *,
        workspace_root: Path | str,
        page_range: str | Sequence[int] | None = None,
    ) -> DocumentObservation:
        raise BackendUnavailableError("base backend cannot extract document text")

    def extract_tables(
        self,
        path: str,
        *,
        workspace_root: Path | str,
        page_number: int | None = None,
    ) -> tuple[DocumentTable, ...]:
        raise BackendUnavailableError("base backend cannot extract document tables")

    def read_page(self, path: str, *, workspace_root: Path | str, page_number: int) -> DocumentPage:
        raise BackendUnavailableError("base backend cannot read document pages")

    def transform_document(
        self,
        request: DocumentTransformRequest,
        *,
        workspace_root: Path | str,
    ) -> DocumentTransformResult:
        raise BackendUnavailableError("base backend cannot transform documents")


class UnsupportedDocumentsBackend(BaseDocumentsBackend):
    """Default backend: every operation fails closed."""

    name = "unsupported"

    def inspect_document(self, path, *, workspace_root):
        raise BackendUnavailableError("no safe document backend is available in this environment")

    def extract_text(self, path, *, workspace_root, page_range=None):
        raise BackendUnavailableError("no safe document backend is available in this environment")

    def extract_tables(self, path, *, workspace_root, page_number=None):
        raise BackendUnavailableError("no safe document backend is available in this environment")

    def read_page(self, path, *, workspace_root, page_number):
        raise BackendUnavailableError("no safe document backend is available in this environment")

    def transform_document(self, request, *, workspace_root):
        raise BackendUnavailableError("no safe document backend is available in this environment")


class MockDocumentsBackend(BaseDocumentsBackend):
    """Deterministic in-memory and filesystem-confined document backend for CI and tests."""

    name = "mock"

    def __init__(self, documents: Mapping[str, _MockDocument] | None = None) -> None:
        self._documents: dict[str, _MockDocument] = dict(documents or {})

    def add_document(self, doc: _MockDocument) -> None:
        self._documents[doc.relative_path.replace("\\", "/")] = doc

    def _get_document(self, path: str, workspace_root: Path | str) -> tuple[Path, str, DocumentType, _MockDocument | bytes]:
        abs_path, norm_path = confine_workspace_path(workspace_root, path)
        assert_not_blocked_extension(norm_path)
        dtype = detect_document_type(norm_path)

        if norm_path in self._documents:
            mock_doc = self._documents[norm_path]
            if mock_doc.has_macros:
                raise DocumentSecurityError("document contains macros and is blocked")
            if mock_doc.is_encrypted:
                raise DocumentSecurityError("encrypted documents cannot be processed")
            if mock_doc.raw_bytes:
                assert_safe_pdf_bytes(mock_doc.raw_bytes)
                bound_document_size(len(mock_doc.raw_bytes))
            return abs_path, norm_path, dtype, mock_doc

        if not abs_path.exists() or not abs_path.is_file():
            raise DocumentNotFoundError(f"document not found: {norm_path}")

        raw = abs_path.read_bytes()
        bound_document_size(len(raw))
        if dtype == DocumentType.PDF:
            assert_safe_pdf_bytes(raw)
        return abs_path, norm_path, dtype, raw

    def inspect_document(self, path: str, *, workspace_root: Path | str) -> DocumentMetadata:
        abs_path, norm_path, dtype, doc_or_bytes = self._get_document(path, workspace_root)
        filename = abs_path.name
        if isinstance(doc_or_bytes, _MockDocument):
            size = len(doc_or_bytes.raw_bytes) if doc_or_bytes.raw_bytes else len(doc_or_bytes.text.encode("utf-8"))
            sha = checksum(doc_or_bytes.raw_bytes) if doc_or_bytes.raw_bytes else checksum(doc_or_bytes.text.encode("utf-8"))
            page_count = len(doc_or_bytes.pages) if doc_or_bytes.pages else (1 if doc_or_bytes.text else 0)
            return DocumentMetadata(
                filename=filename,
                relative_path=norm_path,
                document_type=doc_or_bytes.document_type,
                size_bytes=size,
                sha256=sha,
                page_count=page_count,
                title=doc_or_bytes.title,
                author=doc_or_bytes.author,
                is_encrypted=doc_or_bytes.is_encrypted,
                has_macros=doc_or_bytes.has_macros,
            )
        else:
            raw = doc_or_bytes
            return DocumentMetadata(
                filename=filename,
                relative_path=norm_path,
                document_type=dtype,
                size_bytes=len(raw),
                sha256=checksum(raw),
                page_count=1,
                title=filename,
            )

    def extract_text(
        self,
        path: str,
        *,
        workspace_root: Path | str,
        page_range: str | Sequence[int] | None = None,
    ) -> DocumentObservation:
        abs_path, norm_path, dtype, doc_or_bytes = self._get_document(path, workspace_root)
        meta = self.inspect_document(path, workspace_root=workspace_root)

        if isinstance(doc_or_bytes, _MockDocument):
            mock_doc = doc_or_bytes
            pages = mock_doc.pages
            if not pages and mock_doc.text:
                pages = (DocumentPage(page_number=1, text=mock_doc.text),)

            if page_range is not None:
                selected_nums = validate_page_range(page_range, total_pages=len(pages) or 1)
                selected_pages = tuple(p for p in pages if p.page_number in selected_nums)
            else:
                selected_pages = pages

            combined_text = "\n\n".join(p.text for p in selected_pages if p.text)
            clamped_text = bound_extracted_text(combined_text)

            return DocumentObservation(
                relative_path=norm_path,
                document_type=mock_doc.document_type,
                metadata=meta,
                extracted_text=clamped_text,
                pages=selected_pages,
                tables=mock_doc.tables,
            )
        else:
            raw = doc_or_bytes
            try:
                text_content = raw.decode("utf-8", errors="replace")
            except Exception as exc:
                raise DocumentExtractionError(f"failed to extract text from {norm_path}") from exc
            clamped_text = bound_extracted_text(text_content)
            page = DocumentPage(page_number=1, text=clamped_text)
            return DocumentObservation(
                relative_path=norm_path,
                document_type=dtype,
                metadata=meta,
                extracted_text=clamped_text,
                pages=(page,),
            )

    def extract_tables(
        self,
        path: str,
        *,
        workspace_root: Path | str,
        page_number: int | None = None,
    ) -> tuple[DocumentTable, ...]:
        abs_path, norm_path, dtype, doc_or_bytes = self._get_document(path, workspace_root)
        if isinstance(doc_or_bytes, _MockDocument):
            tables = doc_or_bytes.tables
            if page_number is not None:
                if page_number < 1:
                    raise DocumentFormatError("page_number must be >= 1")
                tables = tuple(t for t in tables if t.page_number == page_number)
            return tables
        return ()

    def read_page(self, path: str, *, workspace_root: Path | str, page_number: int) -> DocumentPage:
        if page_number < 1:
            raise DocumentFormatError("page_number must be >= 1")
        abs_path, norm_path, dtype, doc_or_bytes = self._get_document(path, workspace_root)
        if isinstance(doc_or_bytes, _MockDocument):
            for p in doc_or_bytes.pages:
                if p.page_number == page_number:
                    return p
            raise DocumentFormatError(f"page {page_number} not found in {norm_path}")
        if page_number == 1:
            raw = doc_or_bytes if isinstance(doc_or_bytes, bytes) else b""
            text = bound_extracted_text(raw.decode("utf-8", errors="replace"))
            return DocumentPage(page_number=1, text=text)
        raise DocumentFormatError(f"page {page_number} not found in {norm_path}")

    def transform_document(
        self,
        request: DocumentTransformRequest,
        *,
        workspace_root: Path | str,
    ) -> DocumentTransformResult:
        if not request.input_paths:
            raise DocumentTransformError("at least one input path is required for transformation")
        for in_path in request.input_paths:
            self._get_document(in_path, workspace_root)

        out_dest, out_norm = confine_document_output_path(workspace_root, request.output_path)
        assert_not_blocked_extension(out_norm)

        # Mock transform implementation: combine input text into output file
        combined: list[str] = []
        for in_path in request.input_paths:
            obs = self.extract_text(in_path, workspace_root=workspace_root)
            combined.append(obs.extracted_text)

        output_data = ("\n\n=== TRANSFORM ===\n\n".join(combined)).encode("utf-8")
        bound_document_size(len(output_data))
        out_dest.parent.mkdir(parents=True, exist_ok=True)
        out_dest.write_bytes(output_data)

        return DocumentTransformResult(
            operation=request.operation,
            output_path=out_norm,
            size_bytes=len(output_data),
            sha256=checksum(output_data),
            page_count=len(request.input_paths),
            verified=True,
        )


__all__ = [
    "BaseDocumentsBackend",
    "MockDocumentsBackend",
    "UnsupportedDocumentsBackend",
    "_MockDocument",
    "checksum",
]
