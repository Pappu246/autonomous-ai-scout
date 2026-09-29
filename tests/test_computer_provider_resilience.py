import httpx

from autonomous_agent.computer.ai_controller import OpenAIComputerUseController
from autonomous_agent.computer.backend import MockComputerBackend
from autonomous_agent.computer.connector import BoundedComputerConnector
from autonomous_agent.computer.models import DisplayInfo


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def make_connector() -> BoundedComputerConnector:
    return BoundedComputerConnector(
        backend=MockComputerBackend(display=DisplayInfo(10, 10))
    )


def test_provider_retries_safe_generation_after_transient_timeout(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        httpx.ReadTimeout("temporary"),
        FakeResponse({"id": "retry-1", "output": [], "output_text": "done"}),
    ]
    calls = {"count": 0}

    def fake_post(url, headers, json, timeout):
        calls["count"] += 1
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(
        connector,
        timeout_seconds=5,
        request_retries=2,
        retry_base_seconds=0,
    ).run("inspect the desktop", approved=False, verify_final_state=False)

    assert result.state == "completed"
    assert calls["count"] == 2


def test_provider_does_not_retry_computer_call_output_submission(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-output-1",
                "output": [
                    {
                        "type": "computer_call",
                        "call_id": "call-output-1",
                        "action": {"type": "click", "x": 2, "y": 3, "button": "left"},
                        "pending_safety_checks": [],
                    }
                ],
            }
        ),
    ]
    calls = {"count": 0}

    def fake_post(url, headers, json, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            return responses[0]
        raise httpx.ReadTimeout("output submission failed")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(
        connector,
        timeout_seconds=5,
        request_retries=3,
        retry_base_seconds=0,
    ).run("click the target", approved=True, verify_final_state=False)

    assert result.state == "failed"
    assert "ReadTimeout" in result.reason
    assert calls["count"] == 2


def test_final_verification_provider_failure_is_bounded(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-final-1",
                "output": [
                    {
                        "type": "computer_call",
                        "call_id": "call-final-1",
                        "action": {"type": "click", "x": 2, "y": 3, "button": "left"},
                        "pending_safety_checks": [],
                    }
                ],
            }
        ),
        FakeResponse({"id": "resp-final-2", "output": [], "output_text": "done"}),
    ]

    def fake_post(url, headers, json, timeout):
        if responses:
            return responses.pop(0)
        raise httpx.ReadTimeout("verification timeout")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(
        connector,
        timeout_seconds=5,
        request_retries=1,
        retry_base_seconds=0,
    ).run(
        "inspect the desktop",
        approved=True,
        verify_final_state=True,
    )

    assert result.state == "failed"
    assert result.reason.startswith("final verification request failed:")
