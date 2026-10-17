from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path

import pytest

from autonomous_agent.file_lock import InterProcessFileLock
from autonomous_agent.mission_control import MissionController
from autonomous_agent.vercel_state import state_blob_key


class _FakeBlobNotFoundError(Exception):
    """Matches the not-found exception raised by the Vercel Blob SDK."""


class _FakeBlob:
    def __init__(self, content: bytes):
        self.content = content


class _FakeBlobClient:
    objects: dict[str, bytes] = {}
    mutex = threading.Lock()

    def __init__(self, token=None):
        assert token

    def put(
        self,
        path,
        body,
        *,
        access="private",
        content_type=None,
        add_random_suffix=False,
        overwrite=False,
        token=None,
        **_kwargs,
    ):
        assert access == "private"
        assert token
        with self.mutex:
            if path in self.objects and not overwrite:
                raise RuntimeError("object already exists")
            self.objects[path] = bytes(body)
        return types.SimpleNamespace(pathname=path)

    def get(self, path, *, access="private", use_cache=False, token=None, **_kwargs):
        assert access == "private"
        assert token
        with self.mutex:
            content = self.objects.get(path)
        if content is None:
            raise _FakeBlobNotFoundError(path)
        return _FakeBlob(content)

    def delete(self, path, *, token=None, **_kwargs):
        assert token
        with self.mutex:
            self.objects.pop(path, None)


def _install_fake_blob(monkeypatch) -> None:
    _FakeBlobClient.objects = {}
    module = types.ModuleType("vercel.blob")
    module.BlobClient = _FakeBlobClient
    module.BlobNotFoundError = _FakeBlobNotFoundError
    monkeypatch.setitem(sys.modules, "vercel.blob", module)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-only-token")


def test_missing_remote_state_never_resurrects_stale_local_copy(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"missions":["stale-local-state"]}', encoding="utf-8")
    sync = VercelBlobStateSync(target, timeout_seconds=1)

    with pytest.raises(RuntimeError, match="missing.*local"):
        with sync:
            pass
    assert target.exists(), "retain the local copy for explicit reconciliation"
    assert sync.key not in _FakeBlobClient.objects


def test_approval_claim_is_single_use_across_runtime_instances(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.approved_executor import ApprovalRecord, claim_approval

    _install_fake_blob(monkeypatch)
    approval = ApprovalRecord(
        "action-synthetic", "2026-10-17T00:00:00+00:00",
        "2026-10-18T00:00:00+00:00", "sample" * 8,
    )
    first = tmp_path / "instance-a" / "state" / "approval_claims"
    second = tmp_path / "instance-b" / "state" / "approval_claims"

    assert claim_approval(approval, first).allowed
    assert not claim_approval(approval, second).allowed


def test_timeline_reads_audit_from_other_runtime_instance(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_audit import append_execution_record

    _install_fake_blob(monkeypatch)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = MissionController(root=first_root)
    mission = first.submit("inspect repository")
    append_execution_record(first.audit_path, {
        "execution_id": mission.execution_id,
        "timestamp": "2026-10-17T00:00:00+00:00",
        "state": "running",
        "event": "tool_result",
        "tool": "filesystem.list",
        "verification": "verified",
    })
    second = MissionController(root=second_root)

    timeline = second.timeline(mission.mission_id)

    assert any(event.get("event") == "tool_result" for event in timeline)
    assert any(event.get("event") == "mission_state" for event in timeline)


def test_state_file_is_shared_between_separate_runtime_roots(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    instance_a = tmp_path / "instance-a" / "state" / "missions.json"
    instance_b = tmp_path / "instance-b" / "state" / "missions.json"

    with InterProcessFileLock(instance_a.with_name(instance_a.name + ".lock")):
        instance_a.parent.mkdir(parents=True, exist_ok=True)
        instance_a.write_text(json.dumps({"missions": [{"mission_id": "mission-shared"}]}), encoding="utf-8")

    with InterProcessFileLock(instance_b.with_name(instance_b.name + ".lock")):
        assert json.loads(instance_b.read_text(encoding="utf-8"))["missions"][0]["mission_id"] == "mission-shared"
        payload = json.loads(instance_b.read_text(encoding="utf-8"))
        payload["missions"].append({"mission_id": "mission-from-b"})
        instance_b.write_text(json.dumps(payload), encoding="utf-8")

    with InterProcessFileLock(instance_a.with_name(instance_a.name + ".lock")):
        payload = json.loads(instance_a.read_text(encoding="utf-8"))
        assert [row["mission_id"] for row in payload["missions"]] == ["mission-shared", "mission-from-b"]


def test_mission_submitted_in_one_runtime_is_visible_in_another(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    first_root = tmp_path / "instance-a"
    first_root.mkdir(parents=True)
    first = MissionController(root=first_root)
    mission = first.submit("inspect repository")
    assert mission.state == "pending"

    second_root = tmp_path / "instance-b"
    second_root.mkdir(parents=True)
    second = MissionController(root=second_root)
    persisted = second.store.get(mission.mission_id)

    assert persisted is not None
    assert persisted.mission_id == mission.mission_id
    assert persisted.task == "inspect repository"
    assert second.queue.list()[0].task_id == mission.task_id


def test_file_lock_remains_local_without_blob_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    path = tmp_path / "state" / "missions.json"
    with InterProcessFileLock(path.with_name(path.name + ".lock")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"missions":[]}', encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == {"missions": []}


def test_state_blob_keys_are_isolated_by_vercel_environment(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "missions.json"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/isolated-preview-fixture")
    preview = state_blob_key(path)
    monkeypatch.setenv("VERCEL_ENV", "production")
    production = state_blob_key(path)

    assert preview is not None
    assert production is not None
    assert preview != production
    assert "/preview/" in preview
    assert "/production/" in production


def test_preview_blob_namespace_is_branch_specific_and_preserves_candidate_history(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "missions.json"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "fix/rebased-mission-and-windows-20261012")
    legacy = state_blob_key(path)
    assert legacy == "scout-state/files/preview/missions.json"

    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/f75aea06-autonomous-ai-scout")
    review = state_blob_key(path)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "another-preview-branch")
    other = state_blob_key(path)
    assert review != legacy and other != review and other != legacy
    assert "/preview/" in review and review.endswith("/missions.json")

    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF")
    with pytest.raises(RuntimeError, match="branch ref"):
        state_blob_key(path)


def test_hosted_worker_fails_closed_when_preview_branch_ref_is_missing(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF", raising=False)
    controller = MissionController(root=tmp_path)
    assert not controller.state_backend_ready
    controller.start()
    assert controller._thread is None


def test_preview_branches_cannot_read_each_others_missions(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/first-fixture")
    first_root = tmp_path / "first"
    first_root.mkdir()
    first = MissionController(root=first_root)
    mission = first.submit("inspect repository")

    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/second-fixture")
    second_root = tmp_path / "second"
    second_root.mkdir()
    second = MissionController(root=second_root)
    assert second.store.list() == ()

    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "arena/first-fixture")
    assert first.store.get(mission.mission_id) is not None


def test_non_not_found_blob_read_errors_fail_closed(tmp_path: Path, monkeypatch) -> None:
    _install_fake_blob(monkeypatch)
    controller = MissionController(root=tmp_path)
    key = state_blob_key(controller.store.path)
    assert key is not None

    def unauthorized_get(*_args, **_kwargs):
        raise PermissionError("private blob authorization failed")

    monkeypatch.setattr(_FakeBlobClient, "get", unauthorized_get)
    with pytest.raises(PermissionError, match="authorization failed"):
        with InterProcessFileLock(controller.store.path.with_name(controller.store.path.name + ".lock")):
            pass

def test_remote_lock_retries_when_conflicting_lease_disappears(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    sync = VercelBlobStateSync(target, timeout_seconds=1.0, poll_interval_seconds=0.01)
    assert sync.enabled

    original_put = _FakeBlobClient.put
    conflicts = {"remaining": 2}

    def conflicting_put(self, path, body, **kwargs):
        if path.startswith("scout-state/locks/") and conflicts["remaining"]:
            conflicts["remaining"] -= 1
            raise RuntimeError("Vercel Blob: This blob already exists")
        return original_put(self, path, body, **kwargs)

    monkeypatch.setattr(_FakeBlobClient, "put", conflicting_put)
    sync._acquire_remote_lock()

    assert conflicts["remaining"] == 0
    assert sync._lock_acquired is True
    assert sync._get_bytes(sync.lock_key) is not None
    sync._release_remote_lock()
    assert sync._get_bytes(sync.lock_key) is None



def _http_status_error(status_code: int):
    import httpx

    request = httpx.Request("DELETE", "https://blob.invalid/lock")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("simulated storage status", request=request, response=response)


def test_stale_lease_delete_rate_limit_uses_backoff_and_recovers(tmp_path: Path, monkeypatch) -> None:
    from time import time
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    sync = VercelBlobStateSync(target, timeout_seconds=2.0, poll_interval_seconds=0.01)
    client = sync._blob_client()
    assert sync.lock_key is not None
    stale_payload = json.dumps(
        {"created_at": time() - 300, "target": sync.key},
        separators=(",", ":"),
    ).encode("utf-8")
    client.put(sync.lock_key, stale_payload, overwrite=False, token=sync.token)

    original_delete = _FakeBlobClient.delete
    calls = {"count": 0}

    def throttled_delete(self, path, *, token=None, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _http_status_error(429)
        return original_delete(self, path, token=token, **kwargs)

    monkeypatch.setattr(_FakeBlobClient, "delete", throttled_delete)
    sync._acquire_remote_lock()

    assert calls["count"] >= 2
    assert sync._lock_acquired is True
    sync._release_remote_lock()


def test_releasing_a_lost_lease_never_deletes_another_owners_lease(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    sync = VercelBlobStateSync(target, timeout_seconds=1)
    sync._acquire_remote_lock()
    assert sync.lock_key is not None
    replacement = json.dumps({"created_at": 1792244000, "owner": "other-runtime"}).encode()
    _FakeBlobClient.objects[sync.lock_key] = replacement

    sync._release_remote_lock()
    assert _FakeBlobClient.objects[sync.lock_key] == replacement
    assert sync._lock_acquired is False


def test_lost_lease_cannot_publish_a_stale_state_snapshot(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    sync = VercelBlobStateSync(target, timeout_seconds=1)
    sync.__enter__()
    target.write_text('{"missions":["stale-local-write"]}', encoding="utf-8")
    assert sync.lock_key is not None and sync.key is not None
    _FakeBlobClient.objects[sync.lock_key] = json.dumps(
        {"created_at": 1792244000, "owner": "other-runtime"}
    ).encode()

    with pytest.raises(RuntimeError, match="shared state could not be persisted") as error:
        sync.__exit__(None, None, None)
    assert "lease ownership" in str(error.value.__cause__)
    assert sync.key not in _FakeBlobClient.objects
    assert sync._lock_acquired is False


def test_remote_lease_release_is_idempotent_after_concurrent_delete(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.vercel_state import VercelBlobStateSync

    _install_fake_blob(monkeypatch)
    target = tmp_path / "state" / "missions.json"
    sync = VercelBlobStateSync(target, timeout_seconds=1.0, poll_interval_seconds=0.01)
    sync._acquire_remote_lock()

    def already_deleted(self, path, *, token=None, **kwargs):
        _FakeBlobClient.objects.pop(path, None)
        raise _http_status_error(404)

    monkeypatch.setattr(_FakeBlobClient, "delete", already_deleted)
    sync._release_remote_lock()

    assert sync._lock_acquired is False
