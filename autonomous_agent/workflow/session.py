"""Stateful, bounded cross-domain workflow execution session.

A session owns the execution position of one workflow: which step is current,
which steps completed, which of those were independently verified, which
handoffs already moved data, which steps a human approved, how much of the
action budget is gone, and how deep nested execution has gone.

Everything a session persists is secret-free by construction:

* metadata is validated through the M1 policy, which rejects credential keys
  and credential-looking values outright;
* raw external payloads are never stored -- only their SHA-256 digests, so a
  resume can prove "this artifact already moved" without keeping the content;
* every snapshot field is redacted again on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import (
    MAX_EXECUTION_DEPTH,
    MAX_SESSION_HISTORY,
    MAX_WORKFLOW_ACTION_BUDGET,
    ActionBudget,
    SessionState,
    WorkflowStateError,
    redact_secret,
    redact_structure,
)
from .policy import validate_metadata, validate_step_id, validate_workflow_id


@dataclass(frozen=True)
class WorkflowSessionSnapshot:
    """Serializable, secret-free snapshot of a workflow session."""

    session_id: str
    workflow_id: str
    state: SessionState
    current_step: str = ""
    completed_steps: tuple[str, ...] = ()
    completed_digests: tuple[tuple[str, str], ...] = ()
    verified_steps: tuple[str, ...] = ()
    handoff_digests: tuple[tuple[str, str], ...] = ()
    approved_steps: tuple[str, ...] = ()
    artifact_digests: tuple[tuple[str, str], ...] = ()
    action_limit: int = 0
    action_used: int = 0
    execution_depth: int = 0
    epoch: int = 0
    history: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def safe_dict(self) -> dict[str, Any]:
        """Secret-free serialization suitable for logs, audit records and resume."""
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "state": self.state.value if isinstance(self.state, SessionState) else str(self.state),
            "current_step": self.current_step,
            "completed_steps": list(self.completed_steps),
            "completed_digests": {key: value for key, value in self.completed_digests},
            "verified_steps": list(self.verified_steps),
            "handoff_digests": {key: value for key, value in self.handoff_digests},
            "approved_steps": list(self.approved_steps),
            "artifact_digests": {key: value for key, value in self.artifact_digests},
            "action_limit": self.action_limit,
            "action_used": self.action_used,
            "execution_depth": self.execution_depth,
            "epoch": self.epoch,
            "history": [redact_secret(item) for item in self.history],
            "metadata": redact_structure(dict(self.metadata)),
        }

    def replay_keys(self) -> tuple[str, ...]:
        """Every completed step and handoff digest, for rebuilding replay state."""
        return tuple(sorted({value for _, value in self.completed_digests} | {value for _, value in self.handoff_digests}))


class WorkflowSession:
    """One bounded, stateful cross-domain workflow execution session."""

    def __init__(
        self,
        *,
        session_id: str = "workflow-session",
        workflow_id: str = "",
        action_budget: ActionBudget | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.session_id = str(session_id).strip() or "workflow-session"
        self.workflow_id = validate_workflow_id(workflow_id) if workflow_id else ""
        self.state = SessionState.OPEN
        self.current_step = ""
        self.epoch = 0
        self.execution_depth = 0
        self.history: list[str] = []
        self.metadata: dict[str, Any] = validate_metadata(metadata)
        self.action_budget = action_budget or ActionBudget(limit=MAX_WORKFLOW_ACTION_BUDGET)
        self._completed: dict[str, str] = {}
        self._verified: list[str] = []
        self._handoffs: dict[str, str] = {}
        self._approved: list[str] = []
        self._artifacts: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state in (SessionState.OPEN, SessionState.RESUMED)

    def ensure_open(self) -> None:
        if not self.active:
            raise WorkflowStateError(f"workflow session is not open (state={self.state.value})")

    def bind(self, workflow_id: str) -> None:
        """Bind this session to exactly one workflow, refusing to switch."""
        self.ensure_open()
        resolved = validate_workflow_id(workflow_id)
        if self.workflow_id and self.workflow_id != resolved:
            raise WorkflowStateError(
                f"session is bound to workflow '{self.workflow_id}' and cannot switch to '{resolved}'"
            )
        self.workflow_id = resolved
        self.epoch += 1

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.current_step = ""
        self.epoch += 1

    def suspend(self) -> None:
        if self.active:
            self.state = SessionState.SUSPENDED
            self.epoch += 1

    def resume(self) -> None:
        if self.state is SessionState.CLOSED:
            raise WorkflowStateError("a closed workflow session cannot be resumed")
        if self.state is SessionState.SUSPENDED:
            self.state = SessionState.RESUMED
            self.epoch += 1

    # -- bounds ------------------------------------------------------------
    def consume_action(self, count: int = 1) -> None:
        self.ensure_open()
        self.action_budget.consume(count)

    def enter_step(self, step_id: str) -> None:
        """Mark a step as current and bound nested execution depth."""
        self.ensure_open()
        resolved = validate_step_id(step_id)
        if self.execution_depth + 1 > MAX_EXECUTION_DEPTH:
            raise WorkflowStateError(
                f"workflow execution depth exceeds the bound: {self.execution_depth + 1} > {MAX_EXECUTION_DEPTH}"
            )
        self.execution_depth += 1
        self.current_step = resolved

    def exit_step(self) -> None:
        if self.execution_depth > 0:
            self.execution_depth -= 1

    def note(self, entry: str) -> None:
        self.history.append(redact_secret(str(entry))[:200])
        if len(self.history) > MAX_SESSION_HISTORY:
            self.history = self.history[-MAX_SESSION_HISTORY:]
        self.epoch += 1

    # -- progress ----------------------------------------------------------
    def record_completed(self, step_id: str, digest: str) -> None:
        self.ensure_open()
        resolved = validate_step_id(step_id)
        self._completed[resolved] = str(digest)
        self.note(f"completed:{resolved}")

    def record_verified(self, step_id: str) -> None:
        self.ensure_open()
        resolved = validate_step_id(step_id)
        if resolved not in self._completed:
            raise WorkflowStateError(f"cannot verify a step that has not completed: {resolved}")
        if resolved not in self._verified:
            self._verified.append(resolved)
        self.note(f"verified:{resolved}")

    def record_handoff(self, handoff_id: str, digest: str) -> None:
        self.ensure_open()
        self._handoffs[str(handoff_id)] = str(digest)
        self.note(f"handoff:{handoff_id}")

    def record_artifact(self, artifact_key: str, digest: str) -> None:
        self.ensure_open()
        self._artifacts[str(artifact_key)] = str(digest)

    def grant_approval(self, step_id: str) -> None:
        """Record an explicit, caller-supplied human approval for one step."""
        resolved = validate_step_id(step_id)
        if resolved not in self._approved:
            self._approved.append(resolved)
        self.note(f"approved:{resolved}")

    def is_approved(self, step_id: str) -> bool:
        return str(step_id) in self._approved

    def is_completed(self, step_id: str) -> bool:
        return str(step_id) in self._completed

    def is_verified(self, step_id: str) -> bool:
        return str(step_id) in self._verified

    # -- views -------------------------------------------------------------
    @property
    def completed_steps(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    @property
    def verified_steps(self) -> tuple[str, ...]:
        return tuple(sorted(self._verified))

    @property
    def approved_steps(self) -> tuple[str, ...]:
        return tuple(sorted(self._approved))

    @property
    def handoff_digests(self) -> dict[str, str]:
        return dict(self._handoffs)

    @property
    def artifact_digests(self) -> dict[str, str]:
        return dict(self._artifacts)

    def step_digest(self, step_id: str) -> str:
        return self._completed.get(str(step_id), "")

    # -- checkpoint / resume ----------------------------------------------
    def snapshot(self) -> WorkflowSessionSnapshot:
        """Secret-free checkpoint: digests only, never payloads or credentials."""
        return WorkflowSessionSnapshot(
            session_id=self.session_id,
            workflow_id=self.workflow_id,
            state=self.state,
            current_step=self.current_step,
            completed_steps=self.completed_steps,
            completed_digests=tuple(sorted(self._completed.items())),
            verified_steps=self.verified_steps,
            handoff_digests=tuple(sorted(self._handoffs.items())),
            approved_steps=self.approved_steps,
            artifact_digests=tuple(sorted(self._artifacts.items())),
            action_limit=self.action_budget.limit,
            action_used=self.action_budget.used,
            execution_depth=self.execution_depth,
            epoch=self.epoch,
            history=tuple(self.history),
            metadata=dict(self.metadata),
        )

    @classmethod
    def restore(
        cls,
        snapshot: WorkflowSessionSnapshot,
        *,
        action_budget: ActionBudget | None = None,
    ) -> "WorkflowSession":
        """Rebuild a session from a checkpoint, resuming rather than restarting.

        A restored session keeps every completed and verified step, so the
        connector and the replay protector both refuse to redo finished work.
        """
        if not isinstance(snapshot, WorkflowSessionSnapshot):
            raise WorkflowStateError("restore requires a WorkflowSessionSnapshot")

        claimed_limit = int(snapshot.action_limit or MAX_WORKFLOW_ACTION_BUDGET)
        budget = action_budget or ActionBudget(
            limit=min(max(1, claimed_limit), MAX_WORKFLOW_ACTION_BUDGET)
        )
        # A checkpoint is state, not testimony. Work that the snapshot itself
        # records has already been spent, so the restored usage can never fall
        # below it: a tampered or stale ``action_used`` must not hand a resumed
        # workflow a fresh budget.
        recorded_work = len(snapshot.completed_digests) + len(snapshot.handoff_digests)
        claimed_used = max(0, int(snapshot.action_used))
        budget.used = min(max(claimed_used, recorded_work), budget.limit)

        session = cls(
            session_id=snapshot.session_id,
            workflow_id=snapshot.workflow_id,
            action_budget=budget,
            metadata=dict(snapshot.metadata),
        )
        session.current_step = snapshot.current_step
        session.epoch = snapshot.epoch
        session.execution_depth = 0
        session.history = list(snapshot.history)
        session._completed = {key: value for key, value in snapshot.completed_digests}
        session._verified = [item for item in snapshot.verified_steps]
        session._handoffs = {key: value for key, value in snapshot.handoff_digests}
        session._approved = [item for item in snapshot.approved_steps]
        session._artifacts = {key: value for key, value in snapshot.artifact_digests}
        session.state = (
            SessionState.RESUMED if snapshot.state is SessionState.SUSPENDED else snapshot.state
        )
        return session


__all__ = ["WorkflowSession", "WorkflowSessionSnapshot"]
