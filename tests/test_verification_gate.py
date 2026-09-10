from autonomous_agent.verification_gate import GateStage, run_verification_gate


def _callbacks(events, stop_at=None):
    def make(stage):
        def callback():
            events.append(stage.value)
            return stop_at is None or stage is not stop_at
        return callback
    return {stage.value: make(stage) for stage in GateStage}


def test_gate_runs_all_stages_in_required_order():
    events = []
    cb = _callbacks(events)
    result = run_verification_gate(
        proposal=cb["proposal"],
        validation=cb["validation"],
        tests=cb["tests"],
        security=cb["security"],
        policy=cb["policy"],
        approval=cb["approval"],
        execution=cb["execution"],
        post_tests=cb["post_tests"],
        result_verification=cb["result_verification"],
    )
    assert result.passed
    assert events == [stage.value for stage in GateStage]
    assert result.execution_attempted


def test_gate_stops_before_execution_when_approval_fails():
    events = []
    cb = _callbacks(events, GateStage.APPROVAL)
    result = run_verification_gate(
        proposal=cb["proposal"],
        validation=cb["validation"],
        tests=cb["tests"],
        security=cb["security"],
        policy=cb["policy"],
        approval=cb["approval"],
        execution=cb["execution"],
        post_tests=cb["post_tests"],
        result_verification=cb["result_verification"],
    )
    assert not result.passed
    assert result.stopped_at is GateStage.APPROVAL
    assert events[-1] == "approval"
    assert "execution" not in events
    assert not result.execution_attempted


def test_gate_stops_before_later_stages_on_test_failure():
    events = []
    cb = _callbacks(events, GateStage.TESTS)
    result = run_verification_gate(
        proposal=cb["proposal"],
        validation=cb["validation"],
        tests=cb["tests"],
        security=cb["security"],
        policy=cb["policy"],
        approval=cb["approval"],
        execution=cb["execution"],
        post_tests=cb["post_tests"],
        result_verification=cb["result_verification"],
    )
    assert not result.passed
    assert result.stopped_at is GateStage.TESTS
    assert events == ["proposal", "validation", "tests"]


def test_gate_fails_closed_when_callback_raises():
    events = []

    def proposal():
        events.append("proposal")
        raise RuntimeError("boom")

    result = run_verification_gate(
        proposal=proposal,
        validation=lambda: events.append("validation") or True,
        tests=lambda: True,
        security=lambda: True,
        policy=lambda: True,
        approval=lambda: True,
        execution=lambda: True,
        post_tests=lambda: True,
        result_verification=lambda: True,
    )
    assert not result.passed
    assert result.stopped_at is GateStage.PROPOSAL
    assert not result.execution_attempted
    assert events == ["proposal"]
    assert "gate callback failed" in result.reason


def test_gate_accepts_explanatory_string_results():
    result = run_verification_gate(
        proposal=lambda: "proposal accepted",
        validation=lambda: "validation passed",
        tests=lambda: "tests passed",
        security=lambda: "security passed",
        policy=lambda: "policy passed",
        approval=lambda: "approval present",
        execution=lambda: "execution succeeded",
        post_tests=lambda: "post-tests passed",
        result_verification=lambda: "verified",
    )
    assert result.passed
    assert all(check.detail for check in result.checks)
