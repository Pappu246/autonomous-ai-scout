from pathlib import Path
from tempfile import TemporaryDirectory

from autonomous_agent.persistent_memory import PersistentMemory


def main() -> int:
    with TemporaryDirectory(prefix="scout-n26-") as directory:
        path = Path(directory) / "memory.json"
        memory = PersistentMemory(path)
        memory.record_episode(
            "demo-project",
            "stabilize parser tests",
            outcome="verified",
            summary="parser tests now use deterministic fixtures",
        )
        memory.record_episode(
            "demo-project",
            "rotate api_key=DEMOSECRET",
            outcome="verified",
            summary="authorization token=DEMOSECRET2 rotated safely",
        )

        reopened = PersistentMemory(path)
        matches = reopened.recall("demo-project", "parser deterministic fixtures", kind="episode")
        raw = path.read_text(encoding="utf-8")
        print("N26 Persistent Episodic + Semantic Memory demo")
        print(f"recalled: {len(matches)}")
        if matches:
            print(f"top match: {matches[0].data.get('summary')}")
            print(f"score: {matches[0].score}")
        print(f"secret redacted: {'DEMOSECRET' not in raw and 'DEMOSECRET2' not in raw}")
        return 0 if matches and 'DEMOSECRET' not in raw and 'DEMOSECRET2' not in raw else 1


if __name__ == "__main__":
    raise SystemExit(main())
