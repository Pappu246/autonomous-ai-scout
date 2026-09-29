"""Native OpenAI computer-use loop over the bounded desktop connector.

The model proposes structured UI actions. This module never performs raw OS input
itself: every action is delegated to BoundedComputerConnector, which keeps the
existing coordinate, credential, replay, budget, approval, and platform guards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from .connector import BoundedComputerConnector


_MUTATING_ACTIONS = frozenset(
    {"click", "double_click", "drag", "type", "keypress"}
)


class ComputerUseError(RuntimeError):
    """Raised for bounded computer-use protocol or provider failures."""


@dataclass(frozen=True)
class ComputerUseResult:
    state: str
    reason: str
    turns: int
    actions: int
    response_id: str | None = None
    final_text: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "turns": self.turns,
            "actions": self.actions,
            "response_id": self.response_id,
            "final_text": self.final_text,
        }


class OpenAIComputerUseController:
    """Run bounded structured computer-use actions through the existing connector."""

    def __init__(
        self,
        connector: BoundedComputerConnector,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        model: str | None = None,
        endpoint: str = "https://api.openai.com/v1/responses",
        timeout_seconds: float = 90.0,
    ) -> None:
        self._connector = connector
        self._api_key_env = api_key_env
        self._model = model or os.getenv("OPENAI_COMPUTER_MODEL", "gpt-5.6-sol")
        self._endpoint = endpoint
        self._timeout = max(5.0, min(float(timeout_seconds), 180.0))

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _final_text(response: Mapping[str, Any]) -> str:
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text[:16_384]

        messages: list[str] = []
        for item in response.get("output", []) or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") != "message":
                continue
            for part in item.get("content", []) or []:
                if isinstance(part, Mapping) and part.get("type") in {"output_text", "text"}:
                    text = part.get("text")
                    if isinstance(text, str):
                        messages.append(text)
        return "\n".join(messages)[:16_384]

    @staticmethod
    def _computer_calls(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        calls: list[Mapping[str, Any]] = []
        for item in response.get("output", []) or []:
            if isinstance(item, Mapping) and item.get("type") == "computer_call":
                calls.append(item)
        return calls

    @staticmethod
    def _actions_for_call(call: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """Normalize current singular-action and older batched-action responses."""
        action = call.get("action")
        if isinstance(action, Mapping):
            return [action]
        actions = call.get("actions")
        if isinstance(actions, list):
            return [item for item in actions if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _acknowledged_safety_checks(
        pending_checks: Any,
    ) -> list[dict[str, str]]:
        if not isinstance(pending_checks, list):
            return []
        acknowledgements: list[dict[str, str]] = []
        for check in pending_checks:
            if not isinstance(check, Mapping):
                continue
            ack: dict[str, str] = {}
            for key in ("id", "code", "message"):
                value = check.get(key)
                if isinstance(value, str) and value:
                    ack[key] = value
            if ack:
                acknowledgements.append(ack)
        return acknowledgements

    def _post(self, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        try:
            response = httpx.post(
                self._endpoint,
                headers=self._headers(api_key),
                json=dict(payload),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ComputerUseError(f"OpenAI computer-use request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            detail = response.text[:1000]
            raise ComputerUseError(f"OpenAI computer-use API returned HTTP {response.status_code}: {detail}")

        try:
            decoded = response.json()
        except ValueError as exc:
            raise ComputerUseError("OpenAI computer-use response was not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ComputerUseError("OpenAI computer-use response has an invalid top-level shape")
        return decoded

    def run(
        self,
        task: str,
        *,
        approved: bool = False,
        max_turns: int = 20,
        verify_final_state: bool = True,
    ) -> ComputerUseResult:
        task = str(task).strip()
        if not task:
            return ComputerUseResult("failed", "computer task is empty", 0, 0)

        api_key = os.getenv(self._api_key_env, "").strip()
        if not api_key:
            return ComputerUseResult(
                "unavailable",
                f"required provider credential is unavailable: {self._api_key_env}",
                0,
                0,
            )

        max_turns = max(1, min(int(max_turns), 20))
        previous_response_id: str | None = None
        next_input: Any = task
        total_actions = 0
        final_screenshot: Mapping[str, Any] | None = None

        for turn in range(1, max_turns + 1):
            payload: dict[str, Any] = {
                "model": self._model,
                "tools": [{"type": "computer"}],
                "input": next_input,
            }
            if previous_response_id:
                payload["previous_response_id"] = previous_response_id

            response = self._post(payload, api_key)
            response_id = response.get("id")
            response_id = response_id if isinstance(response_id, str) else previous_response_id
            calls = self._computer_calls(response)

            if not calls:
                final_text = self._final_text(response)
                if total_actions > 0 and verify_final_state:
                    verification_prompt = (
                        "Verify the original desktop task using the latest screenshot and the actions "
                        "already performed. Reply with exactly VERIFIED when the requested task is visibly "
                        "complete. Otherwise reply with NOT_VERIFIED followed by a brief reason. Do not perform "
                        "any additional computer actions during verification."
                    )
                    verification_response = self._post(
                        {
                            "model": self._model,
                            "input": verification_prompt,
                            "previous_response_id": response_id,
                        },
                        api_key,
                    )
                    verification_text = self._final_text(verification_response).strip()
                    upper = verification_text.upper()
                    if upper.startswith("VERIFIED"):
                        return ComputerUseResult(
                            "completed_verified",
                            "model completed the task and a dedicated final screenshot verification passed",
                            turn + 1,
                            total_actions,
                            response_id=(
                                verification_response.get("id")
                                if isinstance(verification_response.get("id"), str)
                                else response_id
                            ),
                            final_text=verification_text[:16_384],
                        )
                    return ComputerUseResult(
                        "completed_unverified",
                        f"dedicated final screenshot verification did not confirm completion: {verification_text[:1000]}",
                        turn + 1,
                        total_actions,
                        response_id=response_id,
                        final_text=verification_text[:16_384],
                    )
                return ComputerUseResult(
                    "completed",
                    "model completed without another computer action",
                    turn,
                    total_actions,
                    response_id=response_id,
                    final_text=final_text,
                )

            outputs: list[dict[str, Any]] = []
            for call in calls:
                call_id = call.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    return ComputerUseResult(
                        "failed",
                        "computer_call did not contain a valid call_id",
                        turn,
                        total_actions,
                        response_id=response_id,
                    )

                pending_checks = call.get("pending_safety_checks") or []
                if pending_checks and not approved:
                    return ComputerUseResult(
                        "requires_approval",
                        "model returned pending safety checks; human approval is required before execution",
                        turn,
                        total_actions,
                        response_id=response_id,
                    )

                actions = self._actions_for_call(call)
                if not actions:
                    return ComputerUseResult(
                        "failed",
                        "computer_call did not contain a supported action object",
                        turn,
                        total_actions,
                        response_id=response_id,
                    )

                if not approved and any(
                    str(action.get("type", "")).lower() in _MUTATING_ACTIONS
                    for action in actions
                ):
                    return ComputerUseResult(
                        "requires_approval",
                        "computer task proposed state-changing UI actions",
                        turn,
                        total_actions,
                        response_id=response_id,
                    )

                try:
                    for action in actions:
                        if not isinstance(action, Mapping):
                            raise ComputerUseError("computer action is not an object")
                        self._connector.execute_cua_action(action, approved=approved)
                        total_actions += 1

                    screenshot = self._connector.screen_capture(include_image=True)
                    image_base64 = screenshot.get("image_base64")
                    if not isinstance(image_base64, str) or not image_base64:
                        raise ComputerUseError("computer screenshot did not include image data")
                    final_screenshot = screenshot
                except Exception as exc:
                    return ComputerUseResult(
                        "failed",
                        f"computer action execution failed: {type(exc).__name__}: {exc}",
                        turn,
                        total_actions,
                        response_id=response_id,
                    )

                output_item: dict[str, Any] = {
                    "type": "computer_call_output",
                    "call_id": call_id,
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": f"data:image/png;base64,{image_base64}",
                        "detail": "original",
                    },
                }
                if pending_checks:
                    if not approved:
                        return ComputerUseResult(
                            "requires_approval",
                            "pending provider safety checks require explicit approval",
                            turn,
                            total_actions,
                            response_id=response_id,
                        )
                    acknowledged = self._acknowledged_safety_checks(pending_checks)
                    if not acknowledged:
                        return ComputerUseResult(
                            "failed",
                            "provider safety checks were present but could not be acknowledged safely",
                            turn,
                            total_actions,
                            response_id=response_id,
                        )
                    output_item["acknowledged_safety_checks"] = acknowledged
                outputs.append(output_item)

            previous_response_id = response_id
            next_input = outputs

        return ComputerUseResult(
            "bounded",
            f"computer-use turn limit reached ({max_turns})",
            max_turns,
            total_actions,
            response_id=previous_response_id,
        )


__all__ = ["ComputerUseError", "ComputerUseResult", "OpenAIComputerUseController"]
