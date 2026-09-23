from pathlib import Path

import pytest

from autonomous_agent.external_side_effects import (
    ExternalSideEffectStore,
    SideEffectError,
    SideEffectState,
    canonical_request_digest,
)


def _store(tmp_path: Path) -> ExternalSideEffectStore:
    return ExternalSideEffectStore(tmp_path / "external-side-effects.json")


def test_claim_persists_and_duplicate_execution_is_blocked(tmp_path: Path):
    store = _store(tmp_path)
    digest = canonical_request_digest("email.send", {"to": "a@example.com", "body": "hello"})
    first = store.claim(key="send-1", operation="email.send", request_digest=digest)
    assert first.allowed
    store.mark_executed("send-1", result_digest="b" * 64)

    reopened = _store(tmp_path)
    duplicate = reopened.claim(key="send-1", operation="email.send", request_digest=digest)
    assert not duplicate.allowed
    assert duplicate.replay_blocked
    assert duplicate.record.state is SideEffectState.EXECUTED
    assert "already been executed" in duplicate.reason


def test_unresolved_reservation_blocks_replay_after_restart(tmp_path: Path):
    store = _store(tmp_path)
    digest = canonical_request_digest("calendar.event.create", {"summary": "meeting"})
    assert store.claim(key="cal-1", operation="calendar.event.create", request_digest=digest).allowed

    reopened = _store(tmp_path)
    decision = reopened.claim(key="cal-1", operation="calendar.event.create", request_digest=digest)
    assert not decision.allowed
    assert "reconciliation is required" in decision.reason


def test_same_key_with_different_request_fails_closed(tmp_path: Path):
    store = _store(tmp_path)
    first_digest = canonical_request_digest("rest.POST", {"url": "https://example.com/a", "body": b"a"})
    second_digest = canonical_request_digest("rest.POST", {"url": "https://example.com/a", "body": b"b"})
    store.claim(key="rest-1", operation="rest.POST", request_digest=first_digest)

    with pytest.raises(SideEffectError, match="different request"):
        store.claim(key="rest-1", operation="rest.POST", request_digest=second_digest)


def test_explicit_unknown_state_is_terminal_for_automatic_replay(tmp_path: Path):
    store = _store(tmp_path)
    digest = canonical_request_digest("email.send", {"to": "a@example.com"})
    store.claim(key="send-unknown", operation="email.send", request_digest=digest)
    store.mark_unknown("send-unknown", reason="transport timeout after request dispatch")

    decision = _store(tmp_path).claim(
        key="send-unknown", operation="email.send", request_digest=digest
    )
    assert not decision.allowed
    assert decision.record.state is SideEffectState.UNKNOWN


def test_failed_side_effect_cannot_be_automatically_retried(tmp_path: Path):
    store = _store(tmp_path)
    digest = canonical_request_digest("rest.POST", {"url": "https://example.com/a", "body": b"a"})
    store.claim(key="rest-1", operation="rest.POST", request_digest=digest)
    store.mark_failed("rest-1", reason="provider returned HTTP 409")

    decision = _store(tmp_path).claim(
        key="rest-1", operation="rest.POST", request_digest=digest
    )
    assert not decision.allowed
    assert decision.record.state is SideEffectState.FAILED


def test_ledger_write_is_atomic_and_durable(tmp_path: Path):
    store = _store(tmp_path)
    digest = canonical_request_digest("email.draft", {"to": "a@example.com", "body": "hello"})
    store.claim(key="draft-1", operation="email.draft", request_digest=digest)
    store.mark_executed("draft-1", result_digest="c" * 64)

    raw = (tmp_path / "external-side-effects.json").read_text(encoding="utf-8")
    assert '"state": "executed"' in raw
    assert not list(tmp_path.glob("external-side-effects.json.*.tmp"))


def test_approved_flag_does_not_change_request_identity():
    one = canonical_request_digest("email.send", {"to": "a@example.com", "approved": True})
    two = canonical_request_digest("email.send", {"to": "a@example.com", "approved": False})
    assert one == two
