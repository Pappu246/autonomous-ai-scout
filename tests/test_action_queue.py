from autonomous_agent.action_queue import ActionStatus, build_action_proposal


def test_sensitive_task_cannot_lower_approval():
    proposal = build_action_proposal("deploy the app", ("inspect",), llm_requires_approval=False)
    assert proposal.requires_approval is True
    assert proposal.status is ActionStatus.PROPOSED


def test_sensitive_step_requires_approval():
    proposal = build_action_proposal("inspect project", ("delete temporary files",), llm_requires_approval=False)
    assert proposal.requires_approval is True
    assert proposal.status is ActionStatus.PROPOSED


def test_read_only_plan_can_complete():
    proposal = build_action_proposal("inspect project", ("list files", "run tests"))
    assert proposal.requires_approval is False
    assert proposal.status is ActionStatus.COMPLETED
