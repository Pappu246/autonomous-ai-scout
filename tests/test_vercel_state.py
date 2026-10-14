from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path

from autonomous_agent.file_lock import InterProcessFileLock
from autonomous_agent.mission_control import MissionController


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
        return None if content is None else _FakeBlob(content)

    def delete(self, path, *, token=None, **_kwargs):
        assert token
        with self.mutex:
            self.objects.pop(path, None)


def _install_fake_blob(monkeypatch) -> None:
    _FakeBlobClient.objects = {}
    module = types.ModuleType("vercel.blob")
    module.BlobClient = _FakeBlobClient
    monkeypatch.setitem(sys.modules, "vercel.blob", module)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-only-token")


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
