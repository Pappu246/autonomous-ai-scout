from pathlib import Path

import pytest

from autonomous_agent.browser_connector import ControlledBrowser
from autonomous_agent.browser_workflow import BrowserAction, BrowserWorkflow, MAX_BROWSER_STEPS


def test_browser_workflow_runs_bounded_sequence():
    calls: list[tuple[str, dict]] = []

    def transport(action, request):
        calls.append((action, dict(request)))
        return {"action": action, "url": request["url"], "title": "Demo", "text": "ok", "verification_status": "verified", "status_code": 200}

    browser = ControlledBrowser({"example.com"}, transport)
    result = BrowserWorkflow(browser).run((
        BrowserAction("open", "https://example.com"),
        BrowserAction("click", "https://example.com", selector="#go"),
        BrowserAction("extract", "https://example.com", fields=("title",)),
    ))

    assert result.success
    assert result.completed_steps == 3
    assert [call[0] for call in calls] == ["open", "click", "extract"]


def test_browser_workflow_rejects_unbounded_sequence():
    browser = ControlledBrowser({"example.com"}, lambda *_: {})
    actions = tuple(BrowserAction("open", "https://example.com") for _ in range(MAX_BROWSER_STEPS + 1))
    result = BrowserWorkflow(browser).run(actions)
    assert not result.success
    assert "step limit" in result.reason


def test_browser_workflow_fails_closed_on_disallowed_host():
    browser = ControlledBrowser({"example.com"}, lambda *_: {})
    result = BrowserWorkflow(browser).run((BrowserAction("open", "https://evil.example"),))
    assert not result.success
    assert result.failed_step == 0


def test_browser_workflow_stops_on_verification_failure():
    def transport(action, request):
        return {"action": action, "url": request["url"], "verification_status": "failed"}

    browser = ControlledBrowser({"example.com"}, transport)
    result = BrowserWorkflow(browser).run((
        BrowserAction("open", "https://example.com"),
        BrowserAction("extract", "https://example.com"),
    ))
    assert not result.success
    assert result.completed_steps == 1
    assert result.failed_step == 0


def test_browser_workflow_requires_actions():
    browser = ControlledBrowser({"example.com"}, lambda *_: {})
    result = BrowserWorkflow(browser).run(())
    assert not result.success
    assert result.failed_step is None
    assert "at least one" in result.reason
