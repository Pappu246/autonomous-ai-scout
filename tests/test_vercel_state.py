"""Blob lease counterexample and enforceable fail-closed/single-host boundaries.

The fake store is deliberately stronger than an eventually consistent service:
even with atomic fake put(overwrite=False), GET followed by unconditional DELETE
can destroy a replacement lease. A passing fake-Blob test is not cloud E2E.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from autonomous_agent.approved_executor import ApprovalRecord, claim_approval
from autonomous_agent.file_lock import FileLockTimeout, InterProcessFileLock
from autonomous_agent.mission_control import MissionController
from autonomous_agent.runtime import run_task
from autonomous_agent.vercel_state import (
    SHARED_STATE_PAUSED_REASON,
    SharedStateCoordinationUnavailable,
    VercelBlobStateSync,
    state_blob_key,
)


class _FakeBlobNotFoundError(Exception):
    pass


class _FakeBlob:
    def __init__(self, content: bytes):
        self.content = content


class _FakeBlobClient:
    objects: dict[str, bytes] = {}
    calls: list[tuple[str, str]] = []
    mutex = threading.Lock()

    def __init__(self, token=None):
        assert token
        self.calls.append(("construct", ""))

    def put(self, path, body, *, overwrite=False, **_kwargs):
        with self.mutex:
            self.calls.append(("put", path))
            if path in self.objects and not overwrite:
                raise RuntimeError("object already exists")
            self.objects[path] = bytes(body)

    def get(self, path, **_kwargs):
        with self.mutex:
            self.calls.append(("get", path))
            value = self.objects.get(path)
        if value is None:
            raise _FakeBlobNotFoundError(path)
        return _FakeBlob(value)

    def delete(self, path, **_kwargs):
        # Mirrors vercel==0.11.6: POST /delete accepts URLs only, no owner,
        # ETag, if-match, fencing token or conditional version argument.
        with self.mutex:
            self.calls.append(("delete", path))
            self.objects.pop(path, None)


def _install_fake_blob(monkeypatch) -> None:
    _FakeBlobClient.objects = {}
    _FakeBlobClient.calls = []
    module = types.ModuleType("vercel.blob")
    module.BlobClient = _FakeBlobClient
    module.BlobNotFoundError = _FakeBlobNotFoundError
    monkeypatch.setitem(sys.modules, "vercel.blob", module)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "synthetic-test-only-token")


def _paused_lock(path: Path) -> None:
    with pytest.raises(SharedStateCoordinationUnavailable, match="atomic shared-state coordination"):
        with InterProcessFileLock(path.with_name(path.name + ".lock")):
            raise AssertionError("uncoordinated file lock entered")


def test_sdk_shape_cannot_conditionally_delete_or_fence_a_write() -> None:
    import inspect
    from vercel.blob import BlobClient

    assert "overwrite" in inspect.signature(BlobClient.put).parameters
    assert "if_none_match" in inspect.signature(BlobClient.get).parameters
    assert set(inspect.signature(BlobClient.delete).parameters) == {"self", "url_or_path", "token"}
    # If an upgraded SDK adds a conditional operation, revisit the coordinator
    # design deliberately; do not silently turn the old protocol back on.


def test_legacy_owner_check_delete_interleaving_destroys_replacement_lease() -> None:
    """Forensic reproducer: this is the bug, NOT a passing coordination test."""
    _FakeBlobClient.objects = {}
    _FakeBlobClient.calls = []
    client = _FakeBlobClient(token="synthetic")
    key = "scout-state/locks/synthetic.json"
    first, replacement = b'{"owner":"first"}', b'{"owner":"replacement"}'
    client.put(key, first, overwrite=False)
    assert client.get(key).content == first  # old owner checks its lease
    client.put(key, replacement, overwrite=True)  # another owner replaces it
    client.delete(key)  # old owner has no conditional delete
    assert key not in client.objects  # replacement was deleted: known failure


def test_missing_remote_state_never_resurrects_stale_local_copy(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    target.parent.mkdir(parents=True)
    before = b'{"missions":["stale-local-state"]}'
    target.write_bytes(before)
    _paused_lock(target)
    with pytest.raises(SharedStateCoordinationUnavailable):
        with VercelBlobStateSync(target):
            target.write_bytes(b"different")
    assert target.read_bytes() == before
    assert _FakeBlobClient.objects == {} and _FakeBlobClient.calls == []


def test_approval_claim_denied_on_two_runtime_roots_without_fencing(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    approval = ApprovalRecord("synthetic-action", "2026-10-17T00:00:00+00:00",
                              "2026-10-18T00:00:00+00:00", "synthetic" * 5)
    first = tmp_path / "a" / "state" / "approval_claims"
    second = tmp_path / "b" / "state" / "approval_claims"
    assert not claim_approval(approval, first).allowed
    assert not claim_approval(approval, second).allowed
    assert not first.exists() and not second.exists()
    assert _FakeBlobClient.calls == []


def test_timeline_refuses_uncoordinated_audit_snapshot(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    root = tmp_path / "instance"
    root.mkdir()
    controller = MissionController(root=root)
    assert not controller.state_backend_ready
    with pytest.raises(SharedStateCoordinationUnavailable):
        controller.timeline("synthetic-mission")
    assert _FakeBlobClient.calls == []


def test_two_runtime_roots_cannot_write_blob_backed_state(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    for instance in ("a", "b"):
        target = tmp_path / instance / "state" / "missions.json"
        _paused_lock(target)
        assert not target.exists()
        assert not target.with_name(target.name + ".lock").exists()
    assert _FakeBlobClient.objects == {} and _FakeBlobClient.calls == []


def test_competing_hosted_workers_never_claim_or_enqueue(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/preview-test")
    controllers = []
    for instance in ("a", "b"):
        root = tmp_path / instance
        root.mkdir()
        controller = MissionController(root=root)
        controller.start()
        controllers.append(controller)
        assert not controller.state_backend_ready
        assert controller._thread is None
        with pytest.raises(SharedStateCoordinationUnavailable):
            controller.submit("inspect repository")
        assert not (root / "state" / "mission_queue.json").exists()
    assert all(not item.queue.path.exists() for item in controllers)
    assert _FakeBlobClient.calls == []


def test_file_lock_remains_single_host_without_blob_or_vercel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "state" / "missions.json"
    with InterProcessFileLock(path.with_name(path.name + ".lock")):
        path.write_text('{"missions":[]}', encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == {"missions": []}


def test_local_lock_blocks_competing_process_then_recovers_after_interruption(tmp_path: Path, monkeypatch) -> None:
    """A single host can recover its POSIX/Win32 file lock after a killed owner."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL"):
        monkeypatch.delenv(name, raising=False)
    lock = tmp_path / "state" / "queue.json.lock"
    marker = tmp_path / "locked"
    code = (
        "from pathlib import Path\n"
        "from autonomous_agent.file_lock import InterProcessFileLock\n"
        "import sys, time\n"
        "with InterProcessFileLock(Path(sys.argv[1])):\n"
        " Path(sys.argv[2]).write_text('holding', encoding='utf-8')\n"
        " time.sleep(10)\n"
    )
    environment = os.environ.copy()
    for name in ("BLOB_READ_WRITE_TOKEN", "VERCEL", "VERCEL_ENV", "VERCEL_URL"):
        environment.pop(name, None)
    process = subprocess.Popen([sys.executable, "-c", code, str(lock), str(marker)], env=environment,
                               cwd=Path(__file__).resolve().parents[1])
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists() and process.poll() is None
        with pytest.raises(FileLockTimeout):
            with InterProcessFileLock(lock, timeout_seconds=0.1):
                raise AssertionError("competing process acquired the same local lock")
    finally:
        process.terminate()
        process.wait(timeout=3)
    with InterProcessFileLock(lock, timeout_seconds=1):
        assert marker.read_text(encoding="utf-8") == "holding"


def test_state_blob_keys_are_isolated_by_environment(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "missions.json"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/isolated-preview-fixture")
    preview = state_blob_key(path)
    monkeypatch.setenv("VERCEL_ENV", "production")
    production = state_blob_key(path)
    assert preview is not None and production is not None and preview != production
    assert "/preview/" in preview and "/production/" in production


def test_preview_key_mapping_preserves_candidate_history_but_not_writes(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "missions.json"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "fix/rebased-mission-and-windows-20261012")
    legacy = state_blob_key(path)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/f75aea06-autonomous-ai-scout")
    review = state_blob_key(path)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "another-preview-branch")
    other = state_blob_key(path)
    assert legacy == "scout-state/files/preview/missions.json"
    assert review != legacy and review != other and other != legacy
    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF")
    with pytest.raises(RuntimeError, match="branch ref"):
        state_blob_key(path)


def test_hosted_worker_paused_even_with_token_and_branch_ref(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/preview")
    controller = MissionController(root=tmp_path)
    assert controller.shared_state_required and controller.shared_state_enabled
    assert not controller.state_backend_ready
    controller.start()
    assert controller._thread is None
    with pytest.raises(SharedStateCoordinationUnavailable, match="atomic"):
        with VercelBlobStateSync(tmp_path / "state" / "missions.json"):
            pass
    assert _FakeBlobClient.calls == []


def test_hosted_worker_paused_without_token_or_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF", raising=False)
    controller = MissionController(root=tmp_path)
    controller.start()
    assert not controller.state_backend_ready and controller._thread is None
    _paused_lock(tmp_path / "state" / "missions.json")


def test_preview_branches_cannot_read_each_others_missions(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    for branch in ("arena/first", "arena/second"):
        monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", branch)
        root = tmp_path / branch.replace("/", "-")
        root.mkdir()
        controller = MissionController(root=root)
        with pytest.raises(SharedStateCoordinationUnavailable):
            controller.store.list()
    assert _FakeBlobClient.calls == []


def test_blob_auth_failure_does_not_fall_back_to_local_state(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"historical-local-copy")
    _paused_lock(target)
    assert target.read_bytes() == b"historical-local-copy"
    assert _FakeBlobClient.calls == []  # no attempt to bypass the coordinator


def test_conflicting_lease_and_retries_are_denied_before_sdk_io(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    lease = "scout-state/locks/synthetic.json"
    _FakeBlobClient.objects[lease] = b'{"owner":"competing-worker"}'
    target = tmp_path / "state" / "missions.json"
    for _ in range(3):
        _paused_lock(target)
    assert _FakeBlobClient.objects[lease] == b'{"owner":"competing-worker"}'
    assert _FakeBlobClient.calls == []
    assert not target.exists()  # retries never fall back to per-container state


def test_stale_lease_and_rate_limited_cleanup_stay_untouched(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    lease = "scout-state/locks/stale.json"
    stale = json.dumps({"created_at": time.time() - 300, "owner": "interrupted"}).encode()
    _FakeBlobClient.objects[lease] = stale
    def forbidden_delete(self, path, **_kwargs):
        raise RuntimeError("simulated 429 during stale lease cleanup")
    monkeypatch.setattr(_FakeBlobClient, "delete", forbidden_delete)
    _paused_lock(tmp_path / "state" / "missions.json")
    assert _FakeBlobClient.objects[lease] == stale
    assert _FakeBlobClient.calls == []


def test_lease_release_cannot_delete_replacement_after_owner_check(tmp_path: Path, monkeypatch) -> None:
    """The red interleaving is prevented by refusing the unfenced protocol."""
    _install_fake_blob(monkeypatch)
    lease = "scout-state/locks/owner.json"
    replacement = b'{"owner":"replacement"}'
    _FakeBlobClient.objects[lease] = replacement
    _paused_lock(tmp_path / "state" / "missions.json")
    assert _FakeBlobClient.objects[lease] == replacement
    assert _FakeBlobClient.calls == []


def test_stale_lease_reaper_cannot_delete_fresh_competing_owner(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    lease = "scout-state/locks/stale.json"
    replacement = b'{"owner":"replacement"}'
    _FakeBlobClient.objects[lease] = json.dumps({"created_at": time.time() - 300}).encode()
    _FakeBlobClient.objects[lease] = replacement  # owner change after stale observation
    _paused_lock(tmp_path / "state" / "missions.json")
    assert _FakeBlobClient.objects[lease] == replacement
    assert _FakeBlobClient.calls == []


def test_failed_cleanup_and_interrupted_write_cannot_publish_partial_state(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    key = state_blob_key(target)
    assert key is not None
    _FakeBlobClient.objects[key] = b'{"missions":["committed"]}'
    def cleanup_fails(self, path, **_kwargs):
        raise RuntimeError("simulated 500 cleanup failure")
    monkeypatch.setattr(_FakeBlobClient, "delete", cleanup_fails)
    with pytest.raises(SharedStateCoordinationUnavailable):
        with InterProcessFileLock(target.with_name(target.name + ".lock")):
            target.write_bytes(b'{"missions":["partial"]}')
            raise ValueError("simulated process interruption")
    assert _FakeBlobClient.objects[key] == b'{"missions":["committed"]}'
    assert not target.exists()
    assert _FakeBlobClient.calls == []


def test_direct_run_task_refuses_unfenced_state_before_any_tool(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    (tmp_path / "README.md").write_text("The local file is synthetic.\n", encoding="utf-8")
    with pytest.raises(SharedStateCoordinationUnavailable) as error:
        run_task("Read file README.md.", root=tmp_path,
                 audit_path=tmp_path / "state" / "audit.jsonl",
                 journal_path=tmp_path / "state" / "runs.jsonl")
    assert str(error.value) == SHARED_STATE_PAUSED_REASON
    assert not (tmp_path / "state").exists()
    assert _FakeBlobClient.calls == []


def test_hosted_http_sso_backend_token_does_not_bypass_paused_state(tmp_path: Path, monkeypatch) -> None:
    """Actual local HTTP requests; synthetic token is not a Vercel SSO session."""
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from autonomous_agent.server import RuntimeHandler

    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/synthetic-preview")
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = True
    server.server_token = "synthetic-backend-token-only-for-tests"
    server.workspace_root = tmp_path
    controller = MissionController(root=tmp_path)
    server.mission_controller = controller
    controller.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path: str, *, authorized: bool, body: bytes | None = None):
        headers = {"X-Autonomous-Scout-Token": server.server_token} if authorized else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            headers=headers, data=body, method="POST" if body is not None else "GET",
        )
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    try:
        status, health = request("/health", authorized=False)
        assert status == 503 and health["status"] == "unavailable"
        assert "atomic" in health["mission_control"]
        assert "workspace_root" not in health
        # Every stateful read path must fail closed before consulting this
        # instance's local mission/memory files or approval queue.
        for path in (
            "/api/missions",
            "/api/missions/mission-synthetic",
            "/api/missions/mission-synthetic/timeline",
            "/api/missions/mission-synthetic/memory",
            "/api/approvals",
            "/run?task=inspect",
        ):
            assert request(path, authorized=False)[0] == 401
            status, payload = request(path, authorized=True)
            assert status == 503 and "unavailable" in payload["error"]
        # Submitting, cancelling, resuming, approving, or rejecting may
        # never fall through into local JSON/approval storage in hosted mode.
        for path, body in (
            ("/api/missions", b'{"task":"Read file README.md and summarize two facts."}'),
            ("/api/missions/mission-synthetic/cancel", b"{}"),
            ("/api/missions/mission-synthetic/resume", b"{}"),
            ("/api/approvals/synthetic/approve", b"{}"),
            ("/api/approvals/synthetic/reject", b"{}"),
        ):
            assert request(path, authorized=False, body=body)[0] == 401
            status, payload = request(path, authorized=True, body=body)
            assert status == 503 and "unavailable" in payload["error"]
        # A missing controller cannot misreport hosted health as ready, nor
        # let approval APIs or direct /run use per-container fallback state.
        server.mission_controller = None
        assert request("/health", authorized=False)[0] == 503
        assert request("/api/approvals/synthetic", authorized=False)[0] == 401
        for path in ("/api/approvals/synthetic", "/run?task=inspect"):
            assert request(path, authorized=True)[0] == 503
        assert not (tmp_path / "state" / "missions.json").exists()
        assert not (tmp_path / "state" / "mission_queue.json").exists()
        assert _FakeBlobClient.calls == []
    finally:
        controller.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
