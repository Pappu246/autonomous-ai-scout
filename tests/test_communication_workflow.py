from autonomous_agent.communication_workflow import CommunicationWorkflow
from autonomous_agent.digital_tool import UniversalDigitalToolLayer


def test_meeting_coordination_builds_read_then_optional_write_steps():
    plan = CommunicationWorkflow().plan_meeting_coordination(
        "coordinate a meeting", include_email_search=True, draft_email=True, create_event=True
    )
    assert [step.tool_name for step in plan.steps] == [
        "email.search", "calendar.find_free_time", "email.draft", "calendar.event.create"
    ]
    assert plan.safe_read_steps == ("email.search", "calendar.find_free_time")
    assert plan.approval_required


def test_read_only_communication_steps_can_be_authorized_without_approval():
    workflow = CommunicationWorkflow(UniversalDigitalToolLayer())
    plan = workflow.plan_meeting_coordination("find a time")
    report = workflow.authorization_report(plan)
    assert all(decision.allowed for _, decision in report)


def test_email_draft_and_event_creation_remain_approval_gated():
    workflow = CommunicationWorkflow()
    plan = workflow.plan_meeting_coordination("meeting", draft_email=True, create_event=True)
    report = dict(workflow.authorization_report(plan))
    assert not report["email.draft"].allowed
    assert not report["calendar.event.create"].allowed
    report_approved = dict(workflow.authorization_report(plan, explicitly_approved=True))
    assert report_approved["email.draft"].allowed
    assert report_approved["calendar.event.create"].allowed


def test_send_is_not_implied_by_meeting_coordination():
    plan = CommunicationWorkflow().plan_meeting_coordination("meeting", draft_email=True, create_event=True)
    assert CommunicationWorkflow().send_is_never_implied(plan)
