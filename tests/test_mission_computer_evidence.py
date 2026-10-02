from __future__ import annotations

import json
from pathlib import Path

from autonomous_agent.mission_control import MissionController


def test_computer_evidence_is_recorded_without_raw_final_text(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    sandbox_result = type(
        "SandboxResult",
        (),
        {
            "command": ("COMPUTER", "computer_use"),
            "output": json.dumps(
                {
                    "state": "completed_verified",
                    "verified": True,
                    "turns": 4,
                    "actions": 7,
                    "reason": "verified",
                    "final_text": "sensitive-looking final text that must not be copied",
                }
            ),
        },
    )()

    result = type("ExecutionResult", (), {"results": (sandbox_result,)})()
    controller._record_computer_evidence("exec-computer", result)

    lines = controller.audit_path.read_text(encoding="utf-8").splitlines()
    assert lines
    event = json.loads(lines[-1])
    assert event["event"] == "computer_evidence"
    evidence = json.loads(event["evidence_json"])
    assert evidence["verified"] is True
    assert evidence["actions"] == 7
    assert "final_text" not in json.dumps(evidence)
