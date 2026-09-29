from __future__ import annotations

import base64

import httpx
import pytest

from autonomous_agent.computer.backend import MockComputerBackend
from autonomous_agent.computer.connector import BoundedComputerConnector
from autonomous_agent.computer.models import ComputerSecurityError, DisplayInfo
from autonomous_agent.computer.ai_controller import OpenAIComputerUseController
from autonomous_agent.tool_registry import REGISTRY


class FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def make_connector() -> BoundedComputerConnector:
    return BoundedComputerConnector(
        backend=MockComputerBackend(display=DisplayInfo(10, 10))
    )


def test_real_png_screenshot_payload_is_returned():
    connector = make_connector()
    result = connector.screen_capture(include_image=True)
    raw = base64.b64decode(result["image_base64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert result["format"] == "png"
    assert result["media_type"] == "image/png"


def test_new_native_actions_dispatch_through_connector():
    connector = make_connector()
    connector.execute_cua_action({"type": "move", "x": 1, "y": 2}, approved=False)
    connector.execute_cua_action(
        {"type": "double_click", "x": 2, "y": 3, "button": "left"},
        approved=True,
    )
    connector.execute_cua_action(
        {"type": "scroll", "x": 2, "y": 3, "scroll_y": -240},
        approved=False,
    )
    connector.execute_cua_action(
        {"type": "drag", "path": [{"x": 1, "y": 1}, {"x": 4, "y": 4}]},
        approved=True,
    )
    connector.execute_cua_action(
        {"type": "keypress", "keys": ["ctrl", "a"]},
        approved=True,
    )
    connector.execute_cua_action({"type": "wait", "milliseconds": 0}, approved=False)

    assert connector.backend.mouse_pos == (4, 4)
    assert connector.backend.click_history[-1]["action"] == "drag"
    assert ("ctrl", "a") in connector.backend.hotkey_history


def test_state_changing_cua_actions_require_approval():
    connector = make_connector()
    with pytest.raises(ComputerSecurityError):
        connector.execute_cua_action({"type": "click", "x": 1, "y": 1}, approved=False)
    with pytest.raises(ComputerSecurityError):
        connector.execute_cua_action({"type": "type", "text": "unsafe"}, approved=False)


def test_computer_use_tool_is_explicitly_approval_gated():
    connector = make_connector()
    result = connector.computer_use("open the browser and search for penguins", approved=False)
    assert result["state"] == "requires_approval"
    assert REGISTRY.get("computer.use") is not None
    assert REGISTRY.get("computer.use").safe_autonomous is False


def test_openai_computer_use_loop_executes_actions_and_returns_screenshots(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "computer_call",
                        "call_id": "call-1",
                        "actions": [
                            {"type": "click", "button": "left", "x": 2, "y": 3},
                        ],
                        "status": "completed",
                    }
                ],
            }
        ),
        FakeResponse(
            {
                "id": "resp-2",
                "output": [],
                "output_text": "done",
            }
        ),
    ]
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(connector, timeout_seconds=5).run(
        "click the test target",
        approved=True,
        max_turns=3,
        verify_final_state=False,
    )

    assert result.state == "completed"
    assert result.actions == 1
    assert result.final_text == "done"
    assert connector.backend.click_history[-1]["x"] == 2
    assert len(calls) == 2
    assert calls[1][2]["previous_response_id"] == "resp-1"
    screenshot_output = calls[1][2]["input"][0]["output"]
    assert screenshot_output["type"] == "computer_screenshot"
    assert screenshot_output["image_url"].startswith("data:image/png;base64,")


def test_openai_current_single_action_shape_is_supported(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-current-1",
                "output": [
                    {
                        "type": "computer_call",
                        "id": "cc-1",
                        "call_id": "call-current-1",
                        "action": {"type": "click", "button": "left", "x": 2, "y": 3},
                        "pending_safety_checks": [],
                        "status": "completed",
                    }
                ],
            }
        ),
        FakeResponse({"id": "resp-current-2", "output": [], "output_text": "done"}),
    ]

    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(connector, timeout_seconds=5).run(
        "click the target",
        approved=True,
        max_turns=3,
        verify_final_state=False,
    )

    assert result.state == "completed"
    assert result.actions == 1
    assert connector.backend.click_history[-1]["x"] == 2
    assert calls[1]["input"][0]["type"] == "computer_call_output"


def test_openai_current_safety_checks_are_acknowledged(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-safe-1",
                "output": [
                    {
                        "type": "computer_call",
                        "id": "cc-safe-1",
                        "call_id": "call-safe-1",
                        "action": {"type": "click", "button": "left", "x": 2, "y": 3},
                        "pending_safety_checks": [
                            {"id": "check-1", "code": "risk", "message": "confirm action"}
                        ],
                        "status": "completed",
                    }
                ],
            }
        ),
        FakeResponse({"id": "resp-safe-2", "output": [], "output_text": "done"}),
    ]
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(connector, timeout_seconds=5).run(
        "perform the approved click",
        approved=True,
        max_turns=3,
    )

    assert result.state == "completed"
    output = calls[1]["input"][0]
    assert output["acknowledged_safety_checks"] == [
        {"id": "check-1", "code": "risk", "message": "confirm action"}
    ]


def test_openai_dedicated_final_verification_marks_verified(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    responses = [
        FakeResponse(
            {
                "id": "resp-v1",
                "output": [
                    {
                        "type": "computer_call",
                        "call_id": "call-v1",
                        "action": {"type": "click", "button": "left", "x": 2, "y": 3},
                        "pending_safety_checks": [],
                        "status": "completed",
                    }
                ],
            }
        ),
        FakeResponse(
            {
                "id": "resp-v2",
                "output": [],
                "output_text": "done",
            }
        ),
        FakeResponse(
            {
                "id": "resp-verify",
                "output": [],
                "output_text": "VERIFIED",
            }
        ),
    ]
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = OpenAIComputerUseController(connector, timeout_seconds=5).run(
        "click the test target",
        approved=True,
        max_turns=3,
    )

    assert result.state == "completed_verified"
    assert result.safe_dict()["verified"] is True
    assert len(calls) == 3
    assert calls[2]["previous_response_id"] == "resp-v2"
def test_openai_pending_safety_checks_stop_unapproved_run(monkeypatch):
    connector = make_connector()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class PendingResponse(FakeResponse):
        def __init__(self):
            super().__init__(
                {
                    "id": "resp-safety",
                    "output": [
                        {
                            "type": "computer_call",
                            "call_id": "call-safety",
                            "pending_safety_checks": [{"id": "check-1"}],
                            "actions": [{"type": "click", "x": 2, "y": 3}],
                            "status": "completed",
                        }
                    ],
                }
            )

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: PendingResponse())
    result = OpenAIComputerUseController(connector, timeout_seconds=5).run(
        "perform a consequential click",
        approved=False,
        max_turns=2,
    )

    assert result.state == "requires_approval"
    assert connector.backend.click_history == []
