"""Bounded application connector -- safe, structured application adapter surface.

Composes backend, session, semantic target resolution, replay protection,
and policy enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..prompt_injection_guard import PromptInjectionGuard
from .backend import (
    BaseApplicationBackend,
    UnsupportedApplicationBackend,
)
from .models import (
    ActionBudget,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationObservation,
    ApplicationSecurityError,
    ApplicationSessionError,
    ApplicationState,
    ApplicationTarget,
    consequential_signal,
    looks_like_secret,
    redact_secret,
)
from .policy import (
    ALLOWED_ADAPTER_COMMANDS,
    assert_not_denylisted,
    assert_safe_flag,
    validate_adapter_command,
    validate_command_arguments,
    validate_text_payload,
)
from .replay import ApplicationReplayProtector
from .session import ApplicationSession
from .target import ApplicationSemanticTargetResolver


class BoundedApplicationConnector:
    """Safe, bounded connector for application adapters."""

    def __init__(
        self,
        backend: BaseApplicationBackend | None = None,
        *,
        workspace_root: Path | str = ".",
        guard: PromptInjectionGuard | None = None,
        replay: ApplicationReplayProtector | None = None,
        action_budget: ActionBudget | None = None,
    ) -> None:
        self._backend = backend if backend is not None else UnsupportedApplicationBackend()
        self._workspace_root = Path(workspace_root)
        self._guard = guard or PromptInjectionGuard()
        self._replay = replay or ApplicationReplayProtector()
        self._resolver = ApplicationSemanticTargetResolver()
        self._session = ApplicationSession(
            workspace_root=self._workspace_root,
            action_budget=action_budget,
        )

    # -- introspection -----------------------------------------------------
    @property
    def backend(self) -> BaseApplicationBackend:
        return self._backend

    @property
    def session(self) -> ApplicationSession:
        return self._session

    @property
    def replay_protector(self) -> ApplicationReplayProtector:
        return self._replay

    @property
    def resolver(self) -> ApplicationSemanticTargetResolver:
        return self._resolver

    def is_live(self) -> bool:
        return not isinstance(self._backend, UnsupportedApplicationBackend)

    # -- session management ------------------------------------------------
    def open_session(self, app_id: str, document_path: str = "") -> ApplicationObservation:
        obs = self._backend.open_application(
            app_id,
            document_path=document_path,
            workspace_root=self._workspace_root,
        )
        self._session.open(app_id, document_path=obs.active_document)
        self._session.consume_action(1)
        return obs

    def close_session(self) -> ApplicationObservation:
        if self._session.state is ApplicationState.CLOSED or not self._session.app_id:
            raise ApplicationSessionError("no application session is currently open")
        self._session.consume_action(1)
        obs = self._backend.close_application(self._session.app_id)
        self._session.close()
        return obs

    def suspend_session(self) -> None:
        self._session.suspend()

    def resume_session(self) -> None:
        self._session.resume()

    # -- discovery and inspection ------------------------------------------
    def list_applications(self) -> tuple[ApplicationDescriptor, ...]:
        return self._backend.list_applications()

    def get_application(self, app_id: str) -> ApplicationDescriptor | None:
        return self._backend.get_application(app_id)

    def observe(self, app_id: str | None = None) -> ApplicationObservation:
        self._session.ensure_open()
        target_app = app_id or self._session.app_id
        if not target_app:
            raise ApplicationSessionError("no application specified or open")
        self._session.consume_action(1)
        obs = self._backend.observe(target_app)
        return ApplicationObservation(
            app_id=obs.app_id,
            session_id=self._session.session_id,
            state=obs.state,
            active_document=redact_secret(obs.active_document),
            status_message=redact_secret(obs.status_message),
            data=obs.data,
            epoch=self._session.epoch,
        )

    # -- targets -----------------------------------------------------------
    def resolve_view(self, view_name: str) -> ApplicationTarget:
        obs = self.observe()
        return self._resolver.resolve_view(obs, view_name)

    def resolve_document(self, document_path: str) -> ApplicationTarget:
        obs = self.observe()
        return self._resolver.resolve_document(obs, document_path)

    # -- command execution -------------------------------------------------
    def execute_command(self, cmd: ApplicationCommand) -> ApplicationObservation:
        self._session.ensure_open()
        if cmd.app_id.strip().lower() != self._session.app_id:
            raise ApplicationSessionError(
                f"command app_id {cmd.app_id!r} does not match active session {self._session.app_id!r}"
            )

        # Consequential check
        conseq = consequential_signal(cmd.command, cmd.payload, *cmd.args)
        if conseq:
            raise ApplicationSecurityError(f"consequential application command blocked: {conseq}")

        # Check replay protection for mutating commands
        mutating_commands = {"write_text", "save_document", "open_document", "close_document"}
        is_mutating = cmd.command.strip().lower() in mutating_commands
        if is_mutating:
            key = self._replay.mutation_key(
                command=cmd.command,
                session_id=self._session.session_id,
                app_id=cmd.app_id,
                args=cmd.args,
                payload=cmd.payload,
                target=cmd.target.safe_dict() if cmd.target else None,
            )
            self._replay.check(key)

        self._session.consume_action(1)
        obs = self._backend.execute_command(cmd, workspace_root=self._workspace_root)
        self._session.note_action(cmd.command)

        if is_mutating:
            self._replay.record(key)

        return ApplicationObservation(
            app_id=obs.app_id,
            session_id=self._session.session_id,
            state=obs.state,
            active_document=redact_secret(obs.active_document),
            status_message=redact_secret(obs.status_message),
            data=obs.data,
            epoch=self._session.epoch,
        )


__all__ = ["BoundedApplicationConnector"]
