from datetime import datetime, timedelta, timezone

from autonomous_agent.action_queue import PendingAction, expire_stale_actions


def test_stale_pending_action_becomes_blocked():
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    queue = [PendingAction("old", "fix bug", ("edit",), "high", "reason", "pending", old)]
    expired = expire_stale_actions(queue)
    assert expired[0].status == "blocked"
    assert expired[0].id == "old"


def test_recent_pending_action_remains_pending():
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    queue = [PendingAction("new", "fix bug", ("edit",), "high", "reason", "pending", recent)]
    assert expire_stale_actions(queue)[0].status == "pending"


def test_non_pending_actions_are_never_expired():
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    queue = [PendingAction("done", "fix bug", ("edit",), "high", "reason", "completed", old)]
    assert expire_stale_actions(queue)[0].status == "completed"
