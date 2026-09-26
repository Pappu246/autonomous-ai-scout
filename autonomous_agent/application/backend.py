"""Application backends for the bounded application adapters domain.

- :class:`BaseApplicationBackend` defines the safe, structured application adapter surface.
- :class:`MockApplicationBackend` is a deterministic in-memory backend for CI and tests.
- :class:`UnsupportedApplicationBackend` is the default when no safe live application adapter is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .models import (
    AdapterType,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationNotFoundError,
    ApplicationObservation,
    ApplicationSecurityError,
    ApplicationSessionError,
    ApplicationState,
    ApplicationTarget,
    BackendUnavailableError,
)
from .policy import (
    ALLOWED_ADAPTER_COMMANDS,
    assert_not_denylisted,
    confine_workspace_path,
    validate_adapter_command,
    validate_command_arguments,
    validate_text_payload,
)


class BaseApplicationBackend:
    """The safe, structured application adapter surface."""

    name = "base"

    def list_applications(self) -> tuple[ApplicationDescriptor, ...]:
        raise BackendUnavailableError("base backend cannot list applications")

    def get_application(self, app_id: str) -> ApplicationDescriptor | None:
        raise BackendUnavailableError("base backend cannot get application descriptor")

    def open_application(
        self,
        app_id: str,
        *,
        document_path: str = "",
        workspace_root: Path | str = ".",
    ) -> ApplicationObservation:
        raise BackendUnavailableError("base backend cannot open applications")

    def close_application(self, app_id: str) -> ApplicationObservation:
        raise BackendUnavailableError("base backend cannot close applications")

    def observe(self, app_id: str) -> ApplicationObservation:
        raise BackendUnavailableError("base backend cannot observe application state")

    def execute_command(
        self,
        cmd: ApplicationCommand,
        *,
        workspace_root: Path | str = ".",
    ) -> ApplicationObservation:
        raise BackendUnavailableError("base backend cannot execute application commands")


class UnsupportedApplicationBackend(BaseApplicationBackend):
    """Default backend: every operation fails closed."""

    name = "unsupported"

    def list_applications(self):
        raise BackendUnavailableError("no safe application backend is available in this environment")

    def get_application(self, app_id):
        raise BackendUnavailableError("no safe application backend is available in this environment")

    def open_application(self, app_id, *, document_path="", workspace_root="."):
        raise BackendUnavailableError("no safe application backend is available in this environment")

    def close_application(self, app_id):
        raise BackendUnavailableError("no safe application backend is available in this environment")

    def observe(self, app_id):
        raise BackendUnavailableError("no safe application backend is available in this environment")

    def execute_command(self, cmd, *, workspace_root="."):
        raise BackendUnavailableError("no safe application backend is available in this environment")


@dataclass
class _MockAppState:
    descriptor: ApplicationDescriptor
    state: ApplicationState = ApplicationState.CLOSED
    active_document: str = ""
    status_message: str = ""
    epoch: int = 0
    buffer: str = ""
    views: tuple[str, ...] = ("main_editor",)
    active_view: str = "main_editor"


class MockApplicationBackend(BaseApplicationBackend):
    """Deterministic in-memory backend for application adapters."""

    name = "mock"

    def __init__(
        self,
        apps: Mapping[str, ApplicationDescriptor] | None = None,
    ) -> None:
        self._apps: dict[str, _MockAppState] = {}
        defaults = apps or {
            "vscode": ApplicationDescriptor(
                app_id="vscode",
                name="Visual Studio Code",
                adapter_type=AdapterType.IDE,
                executable="code",
                supported_extensions=(".py", ".ts", ".js", ".json", ".md", ".txt"),
                allowed_commands=tuple(sorted(ALLOWED_ADAPTER_COMMANDS)),
                requires_approval=True,
                safe_autonomous=False,
                description="VS Code editor adapter",
            ),
            "excel": ApplicationDescriptor(
                app_id="excel",
                name="Microsoft Excel",
                adapter_type=AdapterType.OFFICE,
                executable="excel.exe",
                supported_extensions=(".xlsx", ".xls", ".csv"),
                allowed_commands=tuple(sorted(ALLOWED_ADAPTER_COMMANDS)),
                requires_approval=True,
                safe_autonomous=False,
                description="Excel spreadsheet adapter",
            ),
            "word": ApplicationDescriptor(
                app_id="word",
                name="Microsoft Word",
                adapter_type=AdapterType.OFFICE,
                executable="winword.exe",
                supported_extensions=(".docx", ".doc", ".rtf", ".txt"),
                allowed_commands=tuple(sorted(ALLOWED_ADAPTER_COMMANDS)),
                requires_approval=True,
                safe_autonomous=False,
                description="Word document adapter",
            ),
        }
        for desc in defaults.values():
            self.register_application(desc)

    def register_application(self, desc: ApplicationDescriptor) -> None:
        assert_not_denylisted(desc.executable or desc.name)
        self._apps[desc.app_id.lower()] = _MockAppState(descriptor=desc)

    def list_applications(self) -> tuple[ApplicationDescriptor, ...]:
        return tuple(app.descriptor for app in self._apps.values())

    def get_application(self, app_id: str) -> ApplicationDescriptor | None:
        app = self._apps.get(str(app_id).strip().lower())
        return app.descriptor if app is not None else None

    def _get_app(self, app_id: str) -> _MockAppState:
        clean_id = str(app_id).strip().lower()
        app = self._apps.get(clean_id)
        if app is None:
            raise ApplicationNotFoundError(f"application adapter not found: {app_id}")
        return app

    def open_application(
        self,
        app_id: str,
        *,
        document_path: str = "",
        workspace_root: Path | str = ".",
    ) -> ApplicationObservation:
        app = self._get_app(app_id)
        norm_doc = ""
        if document_path:
            _, norm_doc = confine_workspace_path(workspace_root, document_path)

        app.state = ApplicationState.OPEN
        app.active_document = norm_doc
        app.status_message = f"Opened {app.descriptor.name}"
        app.epoch += 1
        return self._observation(app)

    def close_application(self, app_id: str) -> ApplicationObservation:
        app = self._get_app(app_id)
        app.state = ApplicationState.CLOSED
        app.active_document = ""
        app.status_message = f"Closed {app.descriptor.name}"
        app.epoch += 1
        return self._observation(app)

    def observe(self, app_id: str) -> ApplicationObservation:
        app = self._get_app(app_id)
        return self._observation(app)

    def _observation(self, app: _MockAppState) -> ApplicationObservation:
        return ApplicationObservation(
            app_id=app.descriptor.app_id,
            state=app.state,
            active_document=app.active_document,
            status_message=app.status_message,
            data={
                "buffer_len": len(app.buffer),
                "active_view": app.active_view,
                "views": list(app.views),
            },
            epoch=app.epoch,
        )

    def execute_command(
        self,
        cmd: ApplicationCommand,
        *,
        workspace_root: Path | str = ".",
    ) -> ApplicationObservation:
        app = self._get_app(cmd.app_id)
        if app.state is not ApplicationState.OPEN:
            raise ApplicationSessionError(f"application {cmd.app_id} is not open")

        # Validate command against allowed commands
        validated_cmd = validate_adapter_command(
            cmd.command,
            allowed_commands=frozenset(app.descriptor.allowed_commands) if app.descriptor.allowed_commands else None,
        )
        validate_command_arguments(cmd.args)
        if cmd.payload:
            validate_text_payload(cmd.payload)

        # Mock command execution
        if validated_cmd == "write_text":
            app.buffer += cmd.payload
            app.status_message = f"Wrote {len(cmd.payload)} chars to buffer"
        elif validated_cmd == "open_document":
            if cmd.args:
                _, norm = confine_workspace_path(workspace_root, cmd.args[0])
                app.active_document = norm
                app.status_message = f"Switched document to {norm}"
        elif validated_cmd == "close_document":
            app.active_document = ""
            app.status_message = "Document closed"
        elif validated_cmd == "focus_view":
            if cmd.args:
                app.active_view = cmd.args[0]
                app.status_message = f"Focused view {cmd.args[0]}"
        elif validated_cmd == "save_document":
            app.status_message = "Document saved"
        else:
            app.status_message = f"Executed {validated_cmd}"

        app.epoch += 1
        return self._observation(app)


__all__ = [
    "BaseApplicationBackend",
    "MockApplicationBackend",
    "UnsupportedApplicationBackend",
]
