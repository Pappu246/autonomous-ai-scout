from pathlib import Path

from autonomous_agent.browser_connector import ControlledBrowser, browser_transport_from_mapping
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.task_plan_models import TaskPlan
from autonomous_agent.task_planner import plan_task


def _browser_plan(tool_name: str) -> TaskPlan:
    planned = plan_task("browse a page", granted=[Capability.BROWSER])
    assert planned.executable
    step = planned.steps[0]
    replaced = type(step)(
        step.step_id,
        step.description,
        tool_name,
        step.risk,
        step.authorization,
        step.execution_boundary,
        step.verification,
    )
    return TaskPlan(planned.task, planned.intent, (replaced,), planned.risk, True, planned.reason, planned.audit)


def _browser() -> ControlledBrowser:
    transport = browser_transport_from_mapping(
        {
            "open": {"status_code": 200, "title": "Example", "text": "hello"},
            "click": {"status_code": 200, "title": "Next", "text": "clicked"},
            "extract": {"status_code": 200, "title": "Example", "text": "facts"},
        }
    )
    return ControlledBrowser({"example.com"}, transport)


def test_browser_open_routes_through_safe_executor(tmp_path: Path):
    plan = _browser_plan("browser.open")
    audit = tmp_path / "browser.jsonl"
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.BROWSER],
        audit_path=audit,
        execution_id="browser-open",
        browser_connector=_browser(),
        browser_request={"url": "https://example.com"},
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results[0].operation == "browser"
    assert result.results[0].network_disabled is False
    assert '"status_code":200' in result.results[0].output
    assert verify_execution_audit(audit)


def test_browser_click_and_extract_are_allowlisted(tmp_path: Path):
    for index, tool_name, request, expected in [
        (1, "browser.click", {"url": "https://example.com", "selector": "#next"}, "clicked"),
        (2, "browser.extract", {"url": "https://example.com", "fields": ["title"]}, "facts"),
    ]:
        plan = _browser_plan(tool_name)
        result = execute_plan(
            plan,
            tmp_path,
            granted=[Capability.BROWSER],
            audit_path=tmp_path / f"browser-{index}.jsonl",
            execution_id=f"browser-{index}",
            browser_connector=_browser(),
            browser_request=request,
        )
        assert result.state is ExecutionState.VERIFIED
        assert expected in result.results[0].output


def test_browser_operation_requires_injected_connector(tmp_path: Path):
    plan = _browser_plan("browser.open")
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.BROWSER],
        audit_path=tmp_path / "browser-missing.jsonl",
        execution_id="browser-missing",
        browser_request={"url": "https://example.com"},
    )
    assert result.state is ExecutionState.FAILED
