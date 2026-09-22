from pathlib import Path

from autonomous_agent.context_manager import ContextManager
from autonomous_agent.persistent_memory import PersistentMemory


def test_context_prioritizes_live_task_and_plan(tmp_path: Path):
    packet = ContextManager(max_chars=1000).build(
        "fix parser tests",
        plan="inspect repository then run tests",
        observations=["tests currently fail in parser"],
    )
    assert packet.items[0].kind == "task"
    assert packet.items[1].kind == "plan"
    assert "fix parser tests" in packet.text
    assert len(packet.text) <= 1000


def test_context_reuses_persistent_memory_across_reopen(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = PersistentMemory(path)
    memory.record_episode(
        "project-a",
        "stabilize parser tests",
        outcome="verified",
        summary="parser fixtures make failures deterministic",
    )
    reopened = PersistentMemory(path)
    packet = ContextManager().build(
        "fix parser",
        memory=reopened,
        project="project-a",
        memory_query="parser deterministic fixtures",
    )
    assert any(item.kind == "memory" for item in packet.items)
    assert "parser fixtures" in packet.text


def test_context_is_deterministic_for_same_inputs():
    manager = ContextManager()
    first = manager.build("task", plan="plan", observations=["obs"], pinned=["safety"])
    second = manager.build("task", plan="plan", observations=["obs"], pinned=["safety"])
    assert first.digest == second.digest
    assert first.text == second.text


def test_context_drops_low_priority_items_when_budget_is_small():
    packet = ContextManager(max_chars=1000, max_items=3).build(
        "critical task",
        plan="important plan",
        observations=["x" * 500, "y" * 500, "z" * 500],
    )
    assert packet.dropped_items >= 1
    assert packet.items[0].kind == "task"


def test_context_does_not_persist_raw_sensitive_memory(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = PersistentMemory(path)
    memory.record_episode("project-a", "deploy token=SECRET", outcome="verified")
    packet = ContextManager().build(
        "deploy", memory=memory, project="project-a", memory_query="deploy token"
    )
    assert "SECRET" not in packet.text
    assert "SECRET" not in path.read_text(encoding="utf-8")
