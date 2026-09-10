from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_executor import ApprovalRecord, authorize_execution
from autonomous_agent.capability_policy import Capability
from datetime import datetime, timezone, timedelta


def test_execution_boundary_uses_capability_policy():
    action = PendingAction(
        id="cap-1",
        task="inspect project",
        steps=("inspect repository",),
        risk="low",
        reason="verify capability boundary",
        status="approved",
    )
    now = datetime.now(timezone.utc)
    approval = ApprovalRecord.for_action(action, "token", approved_at=now, ttl=timedelta(minutes=5))
    result = authorize_execution(action, approval, now=now)
    assert result.allowed


def test_high_risk_capability_cannot_be_enabled_by_granting_it():
    from autonomous_agent.capability_policy import check_capability

    result = check_capability(Capability.DEPLOY, {Capability.DEPLOY})
    assert not result.allowed
