from autonomous_agent.capability_policy import Capability
from autonomous_agent.specialist_policy import profile_for, specialist_grants, specialist_for_task
from autonomous_agent.specialist_router import SpecialistRole


def test_coding_specialist_is_limited_to_engineering_capabilities() -> None:
    grants = specialist_grants("inspect repository", SpecialistRole.CODING)
    assert Capability.INSPECT in grants
    assert Capability.EMAIL not in grants
    assert Capability.CALENDAR not in grants


def test_communication_specialist_can_use_mail_and_calendar_only_for_selected_tasks() -> None:
    email_grants = specialist_grants("read email", SpecialistRole.COMMUNICATION)
    assert email_grants == (Capability.EMAIL,)

    calendar_grants = specialist_grants("list calendar events", SpecialistRole.COMMUNICATION)
    assert calendar_grants == (Capability.CALENDAR,)


def test_research_specialist_gets_web_research_but_not_source_write() -> None:
    grants = specialist_grants("research current information", SpecialistRole.RESEARCH)
    assert Capability.WEB_RESEARCH in grants
    assert Capability.SOURCE_WRITE not in grants


def test_specialist_for_task_returns_router_role_and_narrowed_grants() -> None:
    role, grants = specialist_for_task("research the latest project findings")
    assert role is SpecialistRole.RESEARCH
    assert Capability.WEB_RESEARCH in grants


def test_unknown_profile_falls_back_to_general() -> None:
    profile = profile_for(SpecialistRole.GENERAL)
    assert profile.role is SpecialistRole.GENERAL
    assert profile.description


def test_approved_specialist_grants_include_high_risk_registered_tool_capability() -> None:
    grants = specialist_grants(
        "use the computer to complete this task",
        SpecialistRole.COMPUTER,
        include_approval_tools=True,
    )
    assert Capability.COMPUTER in grants
