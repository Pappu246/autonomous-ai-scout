from types import SimpleNamespace

from autonomous_agent.computer.observer import ComputerPostConditionObserver
from autonomous_agent.computer.backend import MockComputerBackend
from autonomous_agent.computer.connector import BoundedComputerConnector


def _execution(evidence):
    return SimpleNamespace(
        success=True,
        has_evidence=True,
        evidence=evidence,
        error="",
    )


def _request(capability_id, arguments=None):
    return SimpleNamespace(
        capability_id=capability_id,
        arguments=arguments or {},
    )


def test_native_computer_use_requires_dedicated_visual_verification():
    observer = ComputerPostConditionObserver(BoundedComputerConnector(backend=MockComputerBackend()))
    observation = observer.observe(
        _request("computer:use"),
        _execution({"state": "completed", "verified": False}),
    )
    assert observation.observed is False


def test_native_computer_use_verified_state_is_observable():
    observer = ComputerPostConditionObserver(BoundedComputerConnector(backend=MockComputerBackend()))
    observation = observer.observe(
        _request("computer:use"),
        _execution({"state": "completed_verified", "verified": True}),
    )
    assert observation.observed is True


def test_bare_double_click_dispatch_is_not_verified():
    observer = ComputerPostConditionObserver(BoundedComputerConnector(backend=MockComputerBackend()))
    observation = observer.observe(
        _request("computer:mouse.double_click"),
        _execution({"action": "click", "success": True}),
    )
    assert observation.observed is False


def test_bounded_wait_has_explicit_observable_completion():
    observer = ComputerPostConditionObserver(BoundedComputerConnector(backend=MockComputerBackend()))
    observation = observer.observe(
        _request("computer:wait"),
        _execution({"action": "wait", "milliseconds": 10, "success": True}),
    )
    assert observation.observed is True
