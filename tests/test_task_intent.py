from autonomous_agent.task_intent import classify_intent
from autonomous_agent.task_plan_models import TaskIntent


def test_inspection_request_with_fixture_is_not_classified_as_change():
    task = (
        "Deeply verify the HIGH finding about a possible hard-coded secret in "
        "tests/test_auth_broker.py. Inspect the relevant file and supporting code. "
        "Determine whether it is a real credential, a test fixture, or a false positive. "
        "Provide the exact affected file(s), line-level evidence where available, "
        "severity, confidence, and a concrete safe remediation. Do not modify any files."
    )
    assert classify_intent(task) is TaskIntent.INSPECT
