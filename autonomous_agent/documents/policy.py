"""Platform-independent security policy, bounds, workspace path confinement, format validation, and active content detection for document processing.

Every function fails closed: on any doubt, invalid format, path escape, or
untrusted active content it raises
:class:`~autonomous_agent.documents.models.DocumentSecurityError` or
:class:`~autonomous_agent.documents.models.DocumentFormatError`.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Sequence

from .models import (
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACTED_TEXT_LENGTH,
    MAX_INPUT_DOCUMENTS,
    MAX_PAGE_COUNT,
    DocumentFormatError,
    DocumentSecurityError,
    DocumentType,
    consequential_signal,
    looks_like_secret,
    redact_secret,
    wrap_untrusted_document_content,
)


ALLOWED_EXTENSIONS_MAP: dict[str, DocumentType] = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCUMENT,
    ".doc": DocumentType.DOCUMENT,
    ".odt": DocumentType.DOCUMENT,
    ".rtf": DocumentType.DOCUMENT,
    ".txt": DocumentType.TEXT,
    ".md": DocumentType.TEXT,
    ".csv": DocumentType.CSV,
    ".tsv": DocumentType.CSV,
    ".xlsx": DocumentType.SPREADSHEET,
    ".xls": DocumentType.SPREADSHEET,
    ".ods": DocumentType.SPREADSHEET,
    ".pptx": DocumentType.PRESENTATION,
    ".ppt": DocumentType.PRESENTATION,
    ".odp": DocumentType.PRESENTATION,
}

BLOCKED_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    # Macro-enabled Office formats
    ".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppam", ".ppsm", ".sldm",
    # Executable / script formats
    ".exe", ".dll", ".so", ".dylib", ".bin", ".com", ".scr", ".msi", ".bat", ".cmd",
    ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".jar", ".py", ".sh", ".pif",
})

DANGEROUS_PDF_TOKENS: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFiles",
    b"/SubmitForm",
    b"/ImportData",
    b"/RichMedia",
    b"/Action",
)

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def detect_document_type(filename_or_path: str) -> DocumentType:
    """Detect document type from filename or path, failing closed on unknown or blocked types."""
    if not isinstance(filename_or_path, str) or not filename_or_path.strip():
        raise DocumentFormatError("filename or path must be a non-empty string")
    ext = PurePosixPath(filename_or_path.strip().replace("\\", "/")).suffix.lower()
    if ext in BLOCKED_DOCUMENT_EXTENSIONS:
        raise DocumentSecurityError(
            f"macro-enabled or executable document extension is blocked: {ext}"
        )
    dtype = ALLOWED_EXTENSIONS_MAP.get(ext)
    if dtype is None:
        raise DocumentFormatError(f"unsupported document format or extension: {ext}")
    return dtype


def assert_not_blocked_extension(filename_or_path: str) -> None:
    """Fail closed if filename has an executable or macro-enabled extension."""
    if not isinstance(filename_or_path, str) or not filename_or_path.strip():
        raise DocumentSecurityError("filename cannot be empty")
    ext = PurePosixPath(filename_or_path.strip().replace("\\", "/")).suffix.lower()
    if ext in BLOCKED_DOCUMENT_EXTENSIONS:
        raise DocumentSecurityError(
            f"macro-enabled or executable document extension is blocked: {ext}"
        )


def validate_document_extension(filename_or_path: str) -> str:
    """Validate that the document has a supported, non-blocked extension."""
    dtype = detect_document_type(filename_or_path)
    return PurePosixPath(filename_or_path.strip().replace("\\", "/")).suffix.lower()


def bound_document_size(size_bytes: int) -> int:
    """Validate document file size against maximum bounds."""
    try:
        val = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise DocumentSecurityError("document size must be an integer") from exc
    if val < 0:
        raise DocumentSecurityError("document size cannot be negative")
    if val > MAX_DOCUMENT_BYTES:
        raise DocumentSecurityError(
            f"document size exceeds the maximum bound: {val} > {MAX_DOCUMENT_BYTES}"
        )
    return val


def bound_page_count(count: int) -> int:
    """Validate page count against maximum bounds."""
    try:
        val = int(count)
    except (TypeError, ValueError) as exc:
        raise DocumentSecurityError("page count must be an integer") from exc
    if val < 0:
        raise DocumentSecurityError("page count cannot be negative")
    if val > MAX_PAGE_COUNT:
        raise DocumentSecurityError(
            f"document page count exceeds maximum bound: {val} > {MAX_PAGE_COUNT}"
        )
    return val


def bound_extracted_text(text: str) -> str:
    """Clamp and redact extracted text."""
    if not isinstance(text, str):
        raise DocumentSecurityError("extracted text must be a string")
    redacted = redact_secret(text)
    if len(redacted) > MAX_EXTRACTED_TEXT_LENGTH:
        return redacted[:MAX_EXTRACTED_TEXT_LENGTH]
    return redacted


def validate_page_range(
    pages_spec: str | Sequence[int],
    total_pages: int | None = None,
) -> tuple[int, ...]:
    """Parse and validate page selection (e.g., '1-3,5' or sequence of 1-based page numbers)."""
    if total_pages is not None:
        total = bound_page_count(total_pages)
    else:
        total = MAX_PAGE_COUNT

    selected: list[int] = []
    if isinstance(pages_spec, str):
        cleaned = pages_spec.strip()
        if not cleaned:
            raise DocumentFormatError("page range specification cannot be empty")
        parts = cleaned.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                sub = part.split("-", 1)
                try:
                    start, end = int(sub[0].strip()), int(sub[1].strip())
                except ValueError as exc:
                    raise DocumentFormatError(f"invalid page range format: {part}") from exc
                if start < 1 or end < start:
                    raise DocumentFormatError(f"invalid page range values: {start}-{end}")
                if end > total:
                    raise DocumentFormatError(
                        f"requested page {end} exceeds total pages ({total})"
                    )
                for p in range(start, end + 1):
                    if p not in selected:
                        selected.append(p)
            else:
                try:
                    p = int(part)
                except ValueError as exc:
                    raise DocumentFormatError(f"invalid page number: {part}") from exc
                if p < 1:
                    raise DocumentFormatError(f"page number must be >= 1: {p}")
                if p > total:
                    raise DocumentFormatError(
                        f"requested page {p} exceeds total pages ({total})"
                    )
                if p not in selected:
                    selected.append(p)
    elif isinstance(pages_spec, (list, tuple)):
        for item in pages_spec:
            try:
                p = int(item)
            except (TypeError, ValueError) as exc:
                raise DocumentFormatError(f"page item must be an integer: {item}") from exc
            if p < 1:
                raise DocumentFormatError(f"page number must be >= 1: {p}")
            if p > total:
                raise DocumentFormatError(
                    f"requested page {p} exceeds total pages ({total})"
                )
            if p not in selected:
                selected.append(p)
    else:
        raise DocumentFormatError("page specification must be a string or sequence of ints")

    if not selected:
        raise DocumentFormatError("no pages selected in page range")
    if len(selected) > MAX_PAGE_COUNT:
        raise DocumentSecurityError(
            f"selected page count exceeds bound: {len(selected)} > {MAX_PAGE_COUNT}"
        )
    return tuple(selected)


def sanitize_document_filename(name: str) -> str:
    """Sanitize filename to a single segment safe filename."""
    if not isinstance(name, str):
        raise DocumentSecurityError("filename must be a string")
    candidate = name.strip().replace("\\", "/").split("/")[-1].replace("\x00", "")
    candidate = _SAFE_FILENAME.sub("_", candidate).strip("._")
    if not candidate or candidate in {".", ".."}:
        return "document.pdf"
    if len(candidate) > 128:
        root, dot, ext = candidate.rpartition(".")
        if dot and len(ext) <= 16:
            candidate = root[: 128 - len(ext) - 1] + "." + ext
        else:
            candidate = candidate[:128]
    return candidate


def confine_workspace_path(workspace_root: Path | str, relative_path: str) -> tuple[Path, str]:
    """Resolve a path strictly inside workspace root, rejecting path traversal."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise DocumentSecurityError("a workspace-relative path is required")
    cleaned = relative_path.strip()
    if len(cleaned) > 1024:
        raise DocumentSecurityError(f"path exceeds maximum allowed length: {len(cleaned)} > 1024")
    if re.match(r"^[a-zA-Z]:", cleaned) or cleaned.startswith(("\\\\", "//")):
        raise DocumentSecurityError("absolute or drive-letter paths are not permitted")
    root = Path(workspace_root).resolve()
    candidate = PurePosixPath(cleaned.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DocumentSecurityError("path traversal is not permitted")
    destination = (root / candidate).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise DocumentSecurityError("path escapes the workspace root") from exc
    return destination, str(candidate)


def confine_document_output_path(
    workspace_root: Path | str,
    filename: str,
) -> tuple[Path, str]:
    """Resolve a sanitized document output destination strictly inside workspace root."""
    safe_name = sanitize_document_filename(filename)
    return confine_workspace_path(workspace_root, safe_name)


def detect_dangerous_pdf_content(content: bytes | str) -> tuple[str, ...]:
    """Scan raw PDF bytes or text for active content tokens (JavaScript, Launch actions, EmbeddedFiles)."""
    if isinstance(content, str):
        raw = content.encode("utf-8", errors="ignore")
    elif isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
    else:
        raise DocumentSecurityError("content must be bytes or string")

    found: list[str] = []
    for token in DANGEROUS_PDF_TOKENS:
        if token in raw:
            found.append(token.decode("ascii", errors="ignore"))
    return tuple(found)


def assert_safe_pdf_bytes(content: bytes) -> None:
    """Fail closed if raw PDF content contains active scripting or executable launch actions."""
    threats = detect_dangerous_pdf_content(content)
    if threats:
        raise DocumentSecurityError(
            f"PDF contains forbidden active/executable content: {', '.join(threats)}"
        )


__all__ = [
    "ALLOWED_EXTENSIONS_MAP",
    "BLOCKED_DOCUMENT_EXTENSIONS",
    "DANGEROUS_PDF_TOKENS",
    "assert_not_blocked_extension",
    "assert_safe_pdf_bytes",
    "bound_document_size",
    "bound_extracted_text",
    "bound_page_count",
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
