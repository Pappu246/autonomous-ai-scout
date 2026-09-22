from pathlib import Path
from tempfile import TemporaryDirectory

from autonomous_agent.context_manager import ContextManager
from autonomous_agent.persistent_memory import PersistentMemory


def main() -> int:
    with TemporaryDirectory(prefix="scout-n27-") as directory:
        path = Path(directory) / "memory.json"
        memory = PersistentMemory(path)
        memory.record_episode(
            "demo-project",
            "stabilize parser tests",
            outcome="verified",
            summary="parser fixtures make failures deterministic",
        )
        reopened = PersistentMemory(path)
        packet = ContextManager(max_chars=2500).build(
            "fix parser failures",
            plan="inspect parser then run tests",
            observations=["latest run failed in parser"],
            memory=reopened,
            project="demo-project",
            memory_query="parser deterministic fixtures",
            pinned=["do not widen permissions"],
        )
        print("N27 Context + Long-Term Memory demo")
        print(f"digest: {packet.digest}")
        print(f"dropped_items: {packet.dropped_items}")
        print(packet.text)
        return 0 if any(item.kind == "memory" for item in packet.items) and len(packet.text) <= 2500 else 1


if __name__ == "__main__":
    raise SystemExit(main())
