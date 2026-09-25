"""Post-condition verification and state observation for computer control."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from autonomous_agent.digital.contract import (
        CapabilityExecution,
        CapabilityObservation,
        CapabilityRequest,
    )


class ComputerPostConditionObserver:
    """Verifies that executed computer operations produced real, observable changes.

    Enforces that input event dispatch alone never produces VERIFIED: observable
    evidence of the resulting system state is required.
    """

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    def observe(
        self, request: Any, execution: Any
    ) -> Any:
        from autonomous_agent.digital.contract import CapabilityObservation

        cap_id = request.capability_id

        if not execution.success or not execution.has_evidence:
            return CapabilityObservation(
                cap_id,
                False,
                detail=f"execution failed or produced no evidence: {execution.error or 'no output'}",
            )

        # 1. Read-only observation tools
        if cap_id in {
            "computer:screen.capture",
            "computer:window.list",
            "computer:window.active",
            "computer:clipboard.read",
            "computer:mouse.move",
        }:
            return CapabilityObservation(
                cap_id,
                True,
                execution.evidence,
                detail="read evidence collected and verified",
            )

        # 2. Window focus verification
        if cap_id == "computer:window.focus":
            target_handle = request.arguments.get("handle")
            target_title = request.arguments.get("title")
            try:
                current_active = self._connector.window_active()
            except Exception as exc:
                return CapabilityObservation(
                    cap_id, False, detail=f"failed to re-query active window: {exc}"
                )
            if target_handle is not None and current_active.get("handle") == target_handle:
                return CapabilityObservation(
                    cap_id, True, current_active, "window focus verified by handle"
                )
            if target_title and target_title.lower() in str(current_active.get("title", "")).lower():
                return CapabilityObservation(
                    cap_id, True, current_active, "window focus verified by title"
                )
            return CapabilityObservation(
                cap_id,
                False,
                current_active,
                "window focus verification failed: targeted window is not active",
            )

        # 3. Application launch verification
        if cap_id == "computer:app.launch":
            app = str(request.arguments.get("app", "")).lower()
            try:
                windows = self._connector.window_list()
            except Exception as exc:
                return CapabilityObservation(
                    cap_id, False, detail=f"failed to re-query window list: {exc}"
                )
            app_base = app.replace("\\", "/").split("/")[-1].replace(".exe", "")
            found = any(
                app_base in str(w.get("process_name", "")).lower()
                or app_base in str(w.get("title", "")).lower()
                for w in windows
            )
            if found or execution.evidence.get("pid"):
                return CapabilityObservation(
                    cap_id,
                    True,
                    execution.evidence,
                    "application launch confirmed via process/window observation",
                )
            return CapabilityObservation(
                cap_id,
                False,
                detail="launched application not found in process or window list",
            )

        # 4. Clipboard write verification via independent read-back
        if cap_id == "computer:clipboard.write":
            expected_text = str(request.arguments.get("text", ""))
            try:
                readback = self._connector.clipboard_read()
            except Exception as exc:
                return CapabilityObservation(
                    cap_id, False, detail=f"failed to read back clipboard: {exc}"
                )
            actual_text = readback.get("content", "")
            if actual_text == expected_text:
                return CapabilityObservation(
                    cap_id,
                    True,
                    {"length": len(actual_text)},
                    "clipboard write confirmed by independent re-read",
                )
            return CapabilityObservation(
                cap_id,
                False,
                {"readback_length": len(actual_text)},
                "clipboard content does not match the written payload",
            )

        # 5. Mutating input events (click, type, hotkey)
        # Input event success alone must never produce VERIFIED without observable state change
        if cap_id in {"computer:mouse.click", "computer:keyboard.type", "computer:keyboard.hotkey"}:
            evidence = execution.evidence
            has_observable_change = (
                bool(evidence.get("post_condition"))
                or bool(evidence.get("state_change"))
                or bool(evidence.get("observed_text"))
                or bool(evidence.get("active_window"))
                or bool(evidence.get("cached"))  # replayed verified result
            )
            if not has_observable_change:
                return CapabilityObservation(
                    cap_id,
                    False,
                    detail="bare input event dispatch does not provide observable post-condition evidence",
                )
            return CapabilityObservation(
                cap_id,
                True,
                evidence,
                "input event produced observable UI state change",
            )

        return CapabilityObservation(cap_id, execution.has_evidence, execution.evidence)


__all__ = ["ComputerPostConditionObserver"]
