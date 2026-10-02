from autonomous_agent.specialist_router import SpecialistRole, choose_specialist


def test_coding_signal_routes_to_coding_role():
    decision = choose_specialist("inspect the GitHub repository and fix failing pytest tests")
    assert decision.role is SpecialistRole.CODING
    assert decision.confidence > 0.45


def test_browser_signal_routes_to_browser_role():
    decision = choose_specialist("open the website and click the pricing page")
    assert decision.role is SpecialistRole.BROWSER


def test_document_signal_routes_to_documents_role():
    decision = choose_specialist("extract tables from the PDF")
    assert decision.role is SpecialistRole.DOCUMENTS


def test_email_calendar_signal_routes_to_communication_role():
    decision = choose_specialist("check Gmail and find a free calendar slot")
    assert decision.role is SpecialistRole.COMMUNICATION


def test_unknown_goal_uses_general_role():
    decision = choose_specialist("do the thing")
    assert decision.role is SpecialistRole.GENERAL
