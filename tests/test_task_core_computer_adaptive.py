

def test_execute_adaptive_forwards_computer_connector(monkeypatch, tmp_path: Path) -> None:
    core = AutonomousTaskCore()
    prepared = core.prepare(
        "control the computer and complete this task",
        granted=(Capability.COMPUTER,),
        explicitly_approved=True,
    )
    captured: dict[str, object] = {}

    def fake_execute_adaptive(plan, root, **kwargs):
        captured.update(kwargs)
        from autonomous_agent.adaptive_execution import AdaptiveExecutionResult
        return AdaptiveExecutionResult(
            ExecutionState.VERIFIED,
            "sentinel",
            1,
            0,
            (),
            (),
            str(tmp_path / "adaptive.jsonl"),
        )

    monkeypatch.setattr("autonomous_agent.task_core.execute_adaptive_plan", fake_execute_adaptive)
    connector = object()
    request = {"computer.use": {"task": "control the computer", "max_turns": 2}}

    result = core.execute_adaptive(
        prepared,
        tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="adaptive-core",
        explicitly_approved=True,
        computer_connector=connector,
        computer_request=request,
    )

    assert result.state is ExecutionState.VERIFIED
    assert captured["computer_connector"] is connector
    assert captured["computer_request"] == request
