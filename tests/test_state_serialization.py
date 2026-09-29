from multiprocessing import Event, Process
from pathlib import Path

import pytest

from autonomous_agent.file_lock import FileLockTimeout, InterProcessFileLock
from autonomous_agent.triggers import TriggerRegistry


def _hold_lock(path: str, ready: Event, release: Event) -> None:
    with InterProcessFileLock(path, timeout_seconds=5):
        ready.set()
        assert release.wait(5)


def test_interprocess_file_lock_serializes_processes(tmp_path: Path) -> None:
    path = str(tmp_path / "shared.lock")
    ready = Event()
    release = Event()
    process = Process(target=_hold_lock, args=(path, ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(FileLockTimeout):
            with InterProcessFileLock(path, timeout_seconds=0.1):
                pass
    finally:
        release.set()
        process.join(5)
    assert process.exitcode == 0
    with InterProcessFileLock(path, timeout_seconds=1):
        pass


def test_persistent_trigger_state_is_reloaded_before_fire(tmp_path: Path) -> None:
    path = tmp_path / "triggers.json"
    first = TriggerRegistry(path=path)
    trigger = first.register("hourly", {"task": "scan"}, cooldown_seconds=3600)

    second = TriggerRegistry(path=path)
    assert second.ready(trigger.trigger_id)
    assert first.fire(trigger.trigger_id)
    assert not second.fire(trigger.trigger_id)


def test_in_memory_trigger_state_remains_available() -> None:
    registry = TriggerRegistry()
    trigger = registry.register("memory", {"task": "test"}, cooldown_seconds=3600)

    assert registry.ready(trigger.trigger_id)
    assert registry.fire(trigger.trigger_id)
    assert not registry.ready(trigger.trigger_id)
