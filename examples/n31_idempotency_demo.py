from pathlib import Path
from tempfile import TemporaryDirectory

from autonomous_agent.idempotency import EffectLedger, IdempotentEffectRunner


def main() -> int:
    with TemporaryDirectory(prefix="scout-n31-") as directory:
        runner = IdempotentEffectRunner(EffectLedger(Path(directory) / "effects.json"))
        calls = []
        first = runner.run("send-1", lambda: calls.append("side-effect") or {"ok": True})
        second = runner.run("send-1", lambda: calls.append("side-effect") or {"ok": True})
        print("N31 Idempotency + Recovery demo")
        print(f"first: state={first.state.value}, executed={first.executed}")
        print(f"second: state={second.state.value}, executed={second.executed}")
        print(f"side-effect calls: {len(calls)}")
        return 0 if len(calls) == 1 and second.executed is False else 1


if __name__ == "__main__":
    raise SystemExit(main())
