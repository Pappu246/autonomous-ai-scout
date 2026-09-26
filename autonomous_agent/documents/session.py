"""Stateful, bounded document processing session.

A session owns the active document reference, operation history, action budget,
and epoch counter for checkpoint/resume.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .models import (
    MAX_ACTION_BUDGET,
    ActionBudget,
    DocumentSessionError,
)


class SessionState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class DocumentsSessionSnapshot:
    """Serializable, secret-free snapshot of a documents session."""

    session_id: str
    state: SessionState
    active_document: str
    epoch: int
    action_count: int
    history: tuple[str, ...]
    completed_actions: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value if isinstance(self.state, SessionState) else str(self.state),
            "active_document": self.active_document,
            "epoch": self.epoch,
            "action_count": self.action_count,
            "history": list(self.history),
            "completed_actions": list(self.completed_actions),
        }


class DocumentsSession:
    """One bounded, stateful documents processing session."""

    def __init__(
        self,
        *,
        session_id: str = "documents-session",
        workspace_root: Path | str = ".",
        action_budget: ActionBudget | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace_root = Path(workspace_root)
        self.state = SessionState.OPEN
        self.active_document = ""
        self.epoch = 0
        self.history: list[str] = []
        self.action_budget = action_budget or ActionBudget(limit=MAX_ACTION_BUDGET)

    # -- lifecycle ---------------------------------------------------------
    def ensure_open(self) -> None:
        if self.state is not SessionState.OPEN:
            raise DocumentSessionError(f"documents session is not open (state={self.state.value})")

    def close(self) -> None:
        self.state = SessionState.CLOSED

    def suspend(self) -> None:
        if self.state is SessionState.OPEN:
            self.state = SessionState.SUSPENDED

    def resume(self) -> None:
        if self.state is SessionState.SUSPENDED:
            self.state = SessionState.OPEN

    # -- bounds & actions --------------------------------------------------
    def consume_action(self, count: int = 1) -> None:
        self.ensure_open()
        self.action_budget.consume(count)

    def note_document(self, path: str) -> None:
        self.ensure_open()
        self.active_document = path
        self.history.append(path)
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.epoch += 1

    # -- checkpoint / resume ----------------------------------------------
    def snapshot(self, *, completed_actions: Iterable[str] = ()) -> DocumentsSessionSnapshot:
        return DocumentsSessionSnapshot(
            session_id=self.session_id,
            state=self.state,
            active_document=self.active_document,
            epoch=self.epoch,
            action_count=self.action_budget.used,
            history=tuple(self.history),
            completed_actions=tuple(completed_actions),
        )

    @classmethod
    def restore(
        cls,
        snapshot: DocumentsSessionSnapshot,
        *,
        workspace_root: Path | str = ".",
        action_budget: ActionBudget | None = None,
    ) -> "DocumentsSession":
        session = cls(
            session_id=snapshot.session_id,
            workspace_root=workspace_root,
            action_budget=action_budget,
        )
        session.active_document = snapshot.active_document
        session.epoch = snapshot.epoch
        session.history = list(snapshot.history)
        session.state = SessionState.OPEN if snapshot.state is SessionState.SUSPENDED else snapshot.state
        return session


__all__ = ["DocumentsSession", "DocumentsSessionSnapshot", "SessionState"]
