from pathlib import Path

from autonomous_agent.idempotency import EffectLedger, EffectState, IdempotentEffectRunner


def test_same_effect_key_executes_only_once(tmp_path: Path):
    runner = IdempotentEffectRunner(EffectLedger(tmp_path / "effects.json"))
    calls = []
    operation = lambda: calls.append("run") or {"ok": True}
    first = runner.run("effect-1", operation, verify=lambda value: value["ok"])
    second = runner.run("effect-1", operation, verify=lambda value: value["ok"])
    assert first.state is EffectState.COMMITTED and first.executed
    assert second.state is EffectState.COMMITTED and not second.executed
    assert calls == ["run"]


def test_verification_failure_is_recorded(tmp_path: Path):
    ledger = EffectLedger(tmp_path / "effects.json")
    result = IdempotentEffectRunner(ledger).run("effect-1", lambda: "bad", verify=lambda _: False)
    assert result.state is EffectState.FAILED
    assert ledger.get("effect-1").state is EffectState.FAILED


def test_prepared_effect_requires_recovery_instead_of_replay(tmp_path: Path):
    ledger = EffectLedger(tmp_path / "effects.json")
    ledger.prepare("effect-1")
    calls = []
    result = IdempotentEffectRunner(ledger).run("effect-1", lambda: calls.append(1) or "done")
    assert result.state is EffectState.PREPARED
    assert not result.executed
    assert calls == []


def test_recovery_commits_confirmed_result_without_reexecuting(tmp_path: Path):
    ledger = EffectLedger(tmp_path / "effects.json")
    ledger.prepare("effect-1")
    runner = IdempotentEffectRunner(ledger)
    result = runner.recover_prepared("effect-1", confirmed_result="already-applied")
    assert result.state is EffectState.COMMITTED
    assert not result.executed
    second = runner.run("effect-1", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    assert second.result == "already-applied"


def test_ledger_persists_across_reopen(tmp_path: Path):
    path = tmp_path / "effects.json"
    ledger = EffectLedger(path)
    ledger.commit("effect-1", {"result": 1})
    reopened = EffectLedger(path)
    assert reopened.get("effect-1").state is EffectState.COMMITTED
