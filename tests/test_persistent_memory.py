from pathlib import Path

from autonomous_agent.persistent_memory import PersistentMemory
from autonomous_agent.task_core import AutonomousTaskCore


def test_episode_persists_and_is_recalled_after_store_reopen(tmp_path: Path):
    path = tmp_path / "memory.json"
    first = PersistentMemory(path)
    assert first.record_episode(
        "project-a",
        "fix flaky parser tests",
        outcome="verified",
        summary="parser tests were stabilized with deterministic fixture handling",
    )

    reopened = PersistentMemory(path)
    matches = reopened.recall("project-a", "parser tests fixture", kind="episode")
    assert matches
    assert matches[0].outcome == "verified"
    assert "fixture" in str(matches[0].data["summary"])


def test_fact_recall_is_relevant_and_ranked(tmp_path: Path):
    memory = PersistentMemory(tmp_path / "memory.json")
    memory.record_fact("project-a", "parser", "uses deterministic fixtures", source="test")
    memory.record_fact("project-a", "database", "uses transactional writes", source="test")
    matches = memory.recall("project-a", "parser deterministic fixtures", kind="fact")
    assert matches
    assert matches[0].data["subject"] == "parser"
    assert matches[0].score > 0.4


def test_sensitive_episode_content_is_redacted(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = PersistentMemory(path)
    memory.record_episode(
        "project-a",
        "debug auth api_key=SECRET123",
        outcome="verified",
        summary="authorization token=TOPSECRET was rotated",
    )
    raw = path.read_text(encoding="utf-8")
    assert "SECRET123" not in raw
    assert "TOPSECRET" not in raw
    assert "[REDACTED]" in raw


def test_task_core_memory_helpers_use_same_persistent_store(tmp_path: Path):
    memory = PersistentMemory(tmp_path / "memory.json")
    core = AutonomousTaskCore()
    assert core.remember_episode(memory, "project-a", "run parser verification", outcome="verified", summary="parser verification passed")
    recalled = core.recall_memory(memory, "project-a", "parser verification")
    assert recalled
    assert recalled[0].outcome == "verified"


def test_memory_respects_bounded_recall(tmp_path: Path):
    memory = PersistentMemory(tmp_path / "memory.json", max_recall=2)
    for index in range(5):
        memory.record_episode("project-a", f"parser verification task {index}", outcome="verified")
    assert len(memory.recall("project-a", "parser verification task")) == 2
