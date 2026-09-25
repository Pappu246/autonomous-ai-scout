"""Stateful, bounded browser session.

A session owns the navigation history stack, the per-session bounds (action,
navigation, redirect, response size) and the host allowlist. It is the unit of
checkpoint/resume: :meth:`BrowserSession.snapshot` produces a secret-free,
serializable :class:`~autonomous_agent.browser.models.SessionSnapshot` and
:meth:`BrowserSession.restore` rebuilds bounded state from one.

A session never repeats a completed mutation on its own; replay protection is
layered on top via :mod:`autonomous_agent.browser.replay`.
"""

from __future__ import annotations

from .models import (
    MAX_ACTION_BUDGET,
    MAX_NAVIGATIONS,
    MAX_REDIRECTS,
    ActionBudget,
    BrowserError,
    NavigationRecord,
    SessionSnapshot,
    SessionState,
)
from .policy import validate_host_allowlist


class BrowserSession:
    """One bounded, stateful browsing session."""

    def __init__(
        self,
        *,
        allowed_hosts,
        session_id: str = "session",
        max_navigations: int = MAX_NAVIGATIONS,
        max_redirects: int = MAX_REDIRECTS,
        action_budget: ActionBudget | None = None,
    ) -> None:
        self.session_id = session_id
        self.allowed_hosts = validate_host_allowlist(allowed_hosts)
        self.max_navigations = max(1, int(max_navigations))
        self.max_redirects = max(0, int(max_redirects))
        self.state = SessionState.OPEN
        self.history: list[NavigationRecord] = []
        self.index = -1
        self.epoch = 0
        self.action_budget = action_budget or ActionBudget(limit=MAX_ACTION_BUDGET)

    # -- lifecycle ---------------------------------------------------------
    def ensure_open(self) -> None:
        if self.state is not SessionState.OPEN:
            raise BrowserError(f"browser session is not open (state={self.state.value})")

    def close(self) -> None:
        self.state = SessionState.CLOSED

    def suspend(self) -> None:
        if self.state is SessionState.OPEN:
            self.state = SessionState.SUSPENDED

    def resume(self) -> None:
        if self.state is SessionState.SUSPENDED:
            self.state = SessionState.OPEN

    # -- bounds ------------------------------------------------------------
    def consume_action(self, count: int = 1) -> None:
        self.ensure_open()
        self.action_budget.consume(count)

    def _note_navigation(self, record: NavigationRecord) -> None:
        if record.redirect_depth > self.max_redirects:
            raise BrowserError(
                f"redirect depth {record.redirect_depth} exceeds session bound {self.max_redirects}"
            )
        # Bounded history: drop oldest entries beyond the navigation bound.
        del self.history[self.index + 1 :]
        self.history.append(record)
        if len(self.history) > self.max_navigations:
            overflow = len(self.history) - self.max_navigations
            del self.history[:overflow]
        self.index = len(self.history) - 1
        self.epoch += 1

    def record_navigation(self, record: NavigationRecord) -> None:
        self.ensure_open()
        self._note_navigation(record)

    def move_back(self) -> NavigationRecord:
        self.ensure_open()
        if self.index <= 0:
            raise BrowserError("no previous page in session history")
        self.index -= 1
        self.epoch += 1
        return self.history[self.index]

    def move_forward(self) -> NavigationRecord:
        self.ensure_open()
        if self.index >= len(self.history) - 1:
            raise BrowserError("no forward page in session history")
        self.index += 1
        self.epoch += 1
        return self.history[self.index]

    def note_reload(self) -> None:
        self.ensure_open()
        self.epoch += 1

    def note_epoch(self) -> None:
        self.epoch += 1

    @property
    def current_url(self) -> str:
        if 0 <= self.index < len(self.history):
            return self.history[self.index].url
        return ""

    # -- checkpoint / resume ----------------------------------------------
    def snapshot(self, *, completed_actions=()) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            state=self.state,
            history=tuple(self.history),
            current_index=self.index,
            epoch=self.epoch,
            action_count=self.action_budget.used,
            navigation_count=len(self.history),
            completed_actions=tuple(completed_actions),
        )

    @classmethod
    def restore(
        cls,
        snapshot: SessionSnapshot,
        *,
        allowed_hosts,
        action_budget: ActionBudget | None = None,
    ) -> "BrowserSession":
        session = cls(
            allowed_hosts=allowed_hosts,
            session_id=snapshot.session_id,
            max_navigations=max(MAX_NAVIGATIONS, len(snapshot.history) or 1),
            action_budget=action_budget,
        )
        session.history = list(snapshot.history)
        session.index = snapshot.current_index
        session.epoch = snapshot.epoch
        session.state = SessionState.OPEN if snapshot.state is SessionState.SUSPENDED else snapshot.state
        return session


__all__ = ["BrowserSession"]
