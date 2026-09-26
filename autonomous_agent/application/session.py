"""Stateful, bounded application adapter session.

A session owns the active application adapter binding, lifecycle state,
action budget, and epoch counter for checkpoint/resume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .models import (
    MAX_ACTION_BUDGET,
    ActionBudget,
    ApplicationSessionError,
    ApplicationSessionSnapshot,
    ApplicationState,
)


class ApplicationSession:
    """One bounded, stateful application adapter session."""

    def __init__(
        self,
        *,
        session_id: str = "app-session",
        app_id: str = "",
        workspace_root: Path | str = ".",
        action_budget: ActionBudget | None = None,
    ) -> None:
        self.session_id = session_id
        self.app_id = app_id.strip().lower()
        self.workspace_root = Path(workspace_root)
        self.state = ApplicationState.OPEN if self.app_id else ApplicationState.CLOSED
        self.active_document = ""
        self.epoch = 0
        self.history: list[str] = []
        self.action_budget = action_budget or ActionBudget(limit=MAX_ACTION_BUDGET)

    # -- lifecycle ---------------------------------------------------------
    def ensure_open(self) -> None:
        if self.state is not ApplicationState.OPEN:
            raise ApplicationSessionError(f"application session is not open (state={self.state.value})")

    def open(self, app_id: str, document_path: str = "") -> None:
        self.app_id = app_id.strip().lower()
        self.active_document = document_path
        self.state = ApplicationState.OPEN
        self.epoch += 1

    def close(self) -> None:
        self.state = ApplicationState.CLOSED
        self.active_document = ""
        self.epoch += 1

    def suspend(self) -> None:
        if self.state is ApplicationState.OPEN:
            self.state = ApplicationState.SUSPENDED

    def resume(self) -> None:
        if self.state is ApplicationState.SUSPENDED:
            self.state = ApplicationState.OPEN

    # -- bounds & actions --------------------------------------------------
    def consume_action(self, count: int = 1) -> None:
        self.ensure_open()
        self.action_budget.consume(count)

    def note_action(self, action_name: str) -> None:
        self.ensure_open()
        self.history.append(action_name)
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.epoch += 1

    # -- checkpoint / resume ----------------------------------------------
    def snapshot(self, *, completed_actions: Iterable[str] = ()) -> ApplicationSessionSnapshot:
        return ApplicationSessionSnapshot(
            session_id=self.session_id,
            app_id=self.app_id,
            state=self.state,
            active_document=self.active_document,
            epoch=self.epoch,
            action_count=self.action_budget.used,
            completed_actions=tuple(completed_actions),
        )

    @classmethod
    def restore(
        cls,
        snapshot: ApplicationSessionSnapshot,
        *,
        workspace_root: Path | str = ".",
        action_budget: ActionBudget | None = None,
    ) -> "ApplicationSession":
        session = cls(
            session_id=snapshot.session_id,
            app_id=snapshot.app_id,
            workspace_root=workspace_root,
            action_budget=action_budget,
        )
        session.active_document = snapshot.active_document
        session.epoch = snapshot.epoch
        session.state = ApplicationState.OPEN if snapshot.state is ApplicationState.SUSPENDED else snapshot.state
        return session


__all__ = ["ApplicationSession"]
