"""Bounded documents connector -- the safe, structured documents domain surface.

Composes backend, session, semantic target resolution, replay protection,
and policy enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..prompt_injection_guard import PromptInjectionGuard
from .backend import (
    BaseDocumentsBackend,
    UnsupportedDocumentsBackend,
)
from .models import (
    ActionBudget,
    DocumentMetadata,
    DocumentObservation,
    DocumentPage,
    DocumentSecurityError,
    DocumentSessionError,
    DocumentTable,
    DocumentTransformError,
    DocumentTransformRequest,
    DocumentTransformResult,
    consequential_signal,
    looks_like_secret,
    redact_secret,
    wrap_untrusted_document_content,
)
from .policy import (
    assert_not_blocked_extension,
    confine_workspace_path,
)
from .replay import DocumentsReplayProtector
from .session import DocumentsSession, SessionState
from .target import DocumentTarget, SemanticDocumentTargetResolver


class BoundedDocumentsConnector:
    """Safe, bounded connector for document processing."""

    def __init__(
        self,
        backend: BaseDocumentsBackend | None = None,
        *,
        workspace_root: Path | str = ".",
        guard: PromptInjectionGuard | None = None,
        replay: DocumentsReplayProtector | None = None,
        action_budget: ActionBudget | None = None,
    ) -> None:
        self._backend = backend if backend is not None else UnsupportedDocumentsBackend()
        self._workspace_root = Path(workspace_root)
        self._guard = guard or PromptInjectionGuard()
        self._replay = replay or DocumentsReplayProtector()
        self._resolver = SemanticDocumentTargetResolver()
        self._session = DocumentsSession(
            workspace_root=self._workspace_root,
            action_budget=action_budget,
        )

    # -- introspection -----------------------------------------------------
    @property
    def backend(self) -> BaseDocumentsBackend:
        return self._backend

    @property
    def session(self) -> DocumentsSession:
        return self._session

    @property
    def replay_protector(self) -> DocumentsReplayProtector:
        return self._replay

    @property
    def resolver(self) -> SemanticDocumentTargetResolver:
        return self._resolver

    def is_live(self) -> bool:
        return not isinstance(self._backend, UnsupportedDocumentsBackend)

    # -- session management ------------------------------------------------
    def open_session(self) -> None:
        if self._session.state is SessionState.CLOSED:
            self._session = DocumentsSession(workspace_root=self._workspace_root)

    def close_session(self) -> None:
        self._session.close()

    def suspend_session(self) -> None:
        self._session.suspend()

    def resume_session(self) -> None:
        self._session.resume()

    # -- operations --------------------------------------------------------
    def inspect(self, path: str) -> DocumentMetadata:
        self._session.ensure_open()
        self._session.consume_action(1)
        meta = self._backend.inspect_document(path, workspace_root=self._workspace_root)
        self._session.note_document(meta.relative_path)
        return meta

    def extract_text(
        self,
        path: str,
        *,
        page_range: str | Sequence[int] | None = None,
    ) -> DocumentObservation:
        self._session.ensure_open()
        self._session.consume_action(1)
        obs = self._backend.extract_text(
            path,
            workspace_root=self._workspace_root,
            page_range=page_range,
        )
        self._session.note_document(obs.relative_path)
        # Wrap untrusted extracted text
        wrapped_text = wrap_untrusted_document_content(obs.extracted_text)
        return DocumentObservation(
            relative_path=obs.relative_path,
            document_type=obs.document_type,
            metadata=obs.metadata,
            extracted_text=wrapped_text,
            pages=obs.pages,
            tables=obs.tables,
            warnings=obs.warnings,
            epoch=self._session.epoch,
        )

    def extract_tables(
        self,
        path: str,
        *,
        page_number: int | None = None,
    ) -> tuple[DocumentTable, ...]:
        self._session.ensure_open()
        self._session.consume_action(1)
        tables = self._backend.extract_tables(
            path,
            workspace_root=self._workspace_root,
            page_number=page_number,
        )
        return tables

    def read_page(self, path: str, *, page_number: int) -> DocumentPage:
        self._session.ensure_open()
        self._session.consume_action(1)
        page = self._backend.read_page(
            path,
            workspace_root=self._workspace_root,
            page_number=page_number,
        )
        return page

    def resolve_target_page(self, path: str, page_number: int) -> DocumentTarget:
        obs = self.extract_text(path)
        return self._resolver.resolve_page(obs, page_number)

    def resolve_target_table(self, path: str, table_id: str) -> DocumentTarget:
        obs = self.extract_text(path)
        return self._resolver.resolve_table(obs, table_id)

    def transform(self, request: DocumentTransformRequest) -> DocumentTransformResult:
        self._session.ensure_open()
        if not request.input_paths:
            raise DocumentTransformError("at least one input path is required for transformation")
        for in_path in request.input_paths:
            confine_workspace_path(self._workspace_root, in_path)
            assert_not_blocked_extension(in_path)
        confine_workspace_path(self._workspace_root, request.output_path)
        assert_not_blocked_extension(request.output_path)

        # Consequential check
        conseq = consequential_signal(str(request.operation), request.output_path)
        if conseq:
            raise DocumentSecurityError(f"consequential document transform blocked: {conseq}")

        key = self._replay.mutation_key(
            operation=str(request.operation),
            session_id=self._session.session_id,
            input_paths=request.input_paths,
            output_path=request.output_path,
            parameters=request.parameters,
        )
        self._replay.check(key)
        self._session.consume_action(1)

        result = self._backend.transform_document(request, workspace_root=self._workspace_root)
        self._replay.record(key)
        self._session.note_document(result.output_path)
        return result


__all__ = ["BoundedDocumentsConnector"]
