"""Deterministic behavior, backend, session, targets, replay, and connector tests for bounded application adapter domain."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.application import (
    ActionBudget,
    ActionBudgetExceededError,
    AdapterType,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationNotFoundError,
    ApplicationObservation,
    ApplicationReplayError,
    ApplicationReplayProtector,
    ApplicationSecurityError,
    ApplicationSemanticTargetResolver,
    ApplicationSession,
    ApplicationSessionError,
    ApplicationSessionSnapshot,
    ApplicationState,
    ApplicationTarget,
    BackendUnavailableError,
    BaseApplicationBackend,
    BoundedApplicationConnector,
    MockApplicationBackend,
    TargetResolutionError,
    UnsupportedApplicationBackend,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def mock_backend() -> MockApplicationBackend:
    return MockApplicationBackend()


@pytest.fixture
def connector(mock_backend: MockApplicationBackend, tmp_path: Path) -> BoundedApplicationConnector:
    return BoundedApplicationConnector(
        backend=mock_backend,
        workspace_root=tmp_path,
        action_budget=ActionBudget(limit=25),
    )


# --------------------------------------------------------------------------
# Base and Unsupported Backends
# --------------------------------------------------------------------------
def test_base_backend_fails_closed(tmp_path: Path):
    base = BaseApplicationBackend()
    cmd = ApplicationCommand("write_text", "vscode", payload="text")
    with pytest.raises(BackendUnavailableError):
        base.list_applications()
    with pytest.raises(BackendUnavailableError):
        base.get_application("vscode")
    with pytest.raises(BackendUnavailableError):
        base.open_application("vscode", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError):
        base.close_application("vscode")
    with pytest.raises(BackendUnavailableError):
        base.observe("vscode")
    with pytest.raises(BackendUnavailableError):
        base.execute_command(cmd, workspace_root=tmp_path)


def test_unsupported_backend_fails_closed(tmp_path: Path):
    unsupported = UnsupportedApplicationBackend()
    cmd = ApplicationCommand("write_text", "vscode", payload="text")
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.list_applications()
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.get_application("vscode")
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.open_application("vscode", workspace_root=tmp_path)
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.close_application("vscode")
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.observe("vscode")
    with pytest.raises(BackendUnavailableError, match="no safe application backend"):
        unsupported.execute_command(cmd, workspace_root=tmp_path)


# --------------------------------------------------------------------------
# Mock Backend Behavior
# --------------------------------------------------------------------------
def test_mock_backend_discovery(mock_backend: MockApplicationBackend):
    apps = mock_backend.list_applications()
    app_ids = {a.app_id for a in apps}
    assert "vscode" in app_ids
    assert "excel" in app_ids
    assert "word" in app_ids

    desc = mock_backend.get_application("vscode")
    assert desc is not None
    assert desc.name == "Visual Studio Code"
    assert desc.adapter_type == AdapterType.IDE

    assert mock_backend.get_application("nonexistent") is None


def test_mock_backend_open_observe_close(mock_backend: MockApplicationBackend, tmp_path: Path):
    obs_open = mock_backend.open_application("vscode", document_path="main.py", workspace_root=tmp_path)
    assert obs_open.state == ApplicationState.OPEN
    assert obs_open.active_document == "main.py"
    assert obs_open.epoch == 1

    obs_cur = mock_backend.observe("vscode")
    assert obs_cur.state == ApplicationState.OPEN
    assert obs_cur.active_document == "main.py"

    obs_close = mock_backend.close_application("vscode")
    assert obs_close.state == ApplicationState.CLOSED
    assert obs_close.active_document == ""
    assert obs_close.epoch == 2


def test_mock_backend_execute_command(mock_backend: MockApplicationBackend, tmp_path: Path):
    mock_backend.open_application("vscode", workspace_root=tmp_path)

    cmd = ApplicationCommand("write_text", "vscode", payload="import sys\n")
    obs = mock_backend.execute_command(cmd, workspace_root=tmp_path)
    assert obs.data["buffer_len"] == len("import sys\n")
    assert obs.epoch == 2

    focus_cmd = ApplicationCommand("focus_view", "vscode", args=("main_editor",))
    obs_focus = mock_backend.execute_command(focus_cmd, workspace_root=tmp_path)
    assert obs_focus.data["active_view"] == "main_editor"


def test_mock_backend_security_boundaries(mock_backend: MockApplicationBackend, tmp_path: Path):
    # Denylisted app registration rejected
    bad_desc = ApplicationDescriptor("sh", "Shell", AdapterType.CUSTOM, executable="/bin/sh")
    with pytest.raises(ApplicationSecurityError, match="denylisted"):
        mock_backend.register_application(bad_desc)

    # Command on closed app
    cmd = ApplicationCommand("write_text", "excel", payload="data")
    with pytest.raises(ApplicationSessionError, match="not open"):
        mock_backend.execute_command(cmd, workspace_root=tmp_path)

    # Unallowed command
    mock_backend.open_application("vscode", workspace_root=tmp_path)
    bad_cmd = ApplicationCommand("run_arbitrary_script", "vscode")
    with pytest.raises(ApplicationSecurityError, match="unsupported or unallowed"):
        mock_backend.execute_command(bad_cmd, workspace_root=tmp_path)

    # Dangerous flag
    bad_flag_cmd = ApplicationCommand("open_document", "vscode", args=("--inspect-brk",))
    with pytest.raises(ApplicationSecurityError, match="dangerous or security-weakening"):
        mock_backend.execute_command(bad_flag_cmd, workspace_root=tmp_path)


# --------------------------------------------------------------------------
# Session Lifecycle & Snapshot / Restore
# --------------------------------------------------------------------------
def test_application_session_lifecycle(tmp_path: Path):
    session = ApplicationSession(workspace_root=tmp_path, action_budget=ActionBudget(limit=10))
    assert session.state == ApplicationState.CLOSED

    session.open("vscode", "src/app.py")
    assert session.state == ApplicationState.OPEN
    assert session.app_id == "vscode"
    assert session.active_document == "src/app.py"

    session.consume_action(2)
    session.note_action("write_text")
    assert session.action_budget.used == 2

    session.suspend()
    assert session.state == ApplicationState.SUSPENDED
    with pytest.raises(ApplicationSessionError, match="not open"):
        session.consume_action(1)

    session.resume()
    assert session.state == ApplicationState.OPEN

    snap = session.snapshot(completed_actions=("write_text",))
    assert snap.app_id == "vscode"
    assert snap.active_document == "src/app.py"
    assert snap.completed_actions == ("write_text",)

    restored = ApplicationSession.restore(snap, workspace_root=tmp_path, action_budget=session.action_budget)
    assert restored.state == ApplicationState.OPEN
    assert restored.app_id == "vscode"
    assert restored.active_document == "src/app.py"


# --------------------------------------------------------------------------
# Semantic Target Resolver
# --------------------------------------------------------------------------
def test_application_target_resolver(mock_backend: MockApplicationBackend, tmp_path: Path):
    resolver = ApplicationSemanticTargetResolver()
    obs = mock_backend.open_application("vscode", document_path="index.js", workspace_root=tmp_path)

    tgt_view = resolver.resolve_view(obs, "main_editor")
    assert tgt_view.view_name == "main_editor"
    assert tgt_view.app_id == "vscode"

    tgt_doc = resolver.resolve_document(obs, "index.js")
    assert tgt_doc.document_path == "index.js"

    # Ensure current
    resolver.ensure_current(tgt_view, obs)
    resolver.ensure_current(tgt_doc, obs)

    # Stale target epoch
    stale_tgt = ApplicationTarget("t1", "vscode", "index.js", "main_editor", epoch=999)
    with pytest.raises(TargetResolutionError, match="stale"):
        resolver.ensure_current(stale_tgt, obs)

    # Foreign target app
    foreign_tgt = ApplicationTarget("t2", "excel", "sheet.xlsx", "grid", epoch=obs.epoch)
    with pytest.raises(TargetResolutionError, match="does not match"):
        resolver.ensure_current(foreign_tgt, obs)


# --------------------------------------------------------------------------
# Replay Protection
# --------------------------------------------------------------------------
def test_application_replay_protector():
    replay = ApplicationReplayProtector()
    key = replay.mutation_key(
        command="write_text",
        session_id="sess-1",
        app_id="vscode",
        payload="text content",
    )
    assert replay.already_completed(key) is False
    replay.check(key)  # passes

    replay.record(key)
    assert replay.already_completed(key) is True
    with pytest.raises(ApplicationReplayError, match="refusing to repeat"):
        replay.check(key)

    replay.reset()
    assert replay.already_completed(key) is False


# --------------------------------------------------------------------------
# Connector Operations & Boundaries
# --------------------------------------------------------------------------
def test_connector_lifecycle_and_observation(connector: BoundedApplicationConnector):
    apps = connector.list_applications()
    assert len(apps) >= 3

    obs_open = connector.open_session("vscode", "test.py")
    assert obs_open.state == ApplicationState.OPEN

    obs = connector.observe()
    assert obs.app_id == "vscode"

    obs_close = connector.close_session()
    assert obs_close.state == ApplicationState.CLOSED


def test_connector_execute_and_replay(connector: BoundedApplicationConnector):
    connector.open_session("vscode")

    cmd = ApplicationCommand("write_text", "vscode", payload="print('hello')")
    obs = connector.execute_command(cmd)
    assert obs.data["buffer_len"] > 0

    # Replay on duplicate mutation
    with pytest.raises(ApplicationReplayError, match="refusing to repeat"):
        connector.execute_command(cmd)


def test_connector_consequential_blocking(connector: BoundedApplicationConnector):
    connector.open_session("vscode")
    cmd = ApplicationCommand("write_text", "vscode", payload="delete account permanently")
    with pytest.raises(ApplicationSecurityError, match="consequential"):
        connector.execute_command(cmd)


def test_connector_action_budget_exhaustion(mock_backend: MockApplicationBackend, tmp_path: Path):
    conn = BoundedApplicationConnector(
        backend=mock_backend,
        workspace_root=tmp_path,
        action_budget=ActionBudget(limit=2),
    )
    conn.open_session("vscode")
    conn.observe()
    with pytest.raises(ActionBudgetExceededError, match="budget exceeded"):
        conn.execute_command(ApplicationCommand("write_text", "vscode", payload="hi"))


def test_connector_unsupported_backend_fails_closed(tmp_path: Path):
    conn = BoundedApplicationConnector(workspace_root=tmp_path)
    assert conn.is_live() is False
    with pytest.raises(BackendUnavailableError):
        conn.open_session("vscode")
