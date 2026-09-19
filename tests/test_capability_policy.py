from autonomous_agent.capability_policy import Capability, authorize_tool, check_capability


def test_safe_capability_requires_explicit_grant():
    denied = check_capability(Capability.TEST)
    assert not denied.allowed
    granted = check_capability(Capability.TEST, {Capability.TEST})
    assert granted.allowed


def test_high_risk_capabilities_are_permanently_denied():
    for capability in (
        Capability.NETWORK,
        Capability.SECRETS,
        Capability.BILLING,
        Capability.SOURCE_WRITE,
        Capability.MERGE,
        Capability.DEPLOY,
        Capability.DESTRUCTIVE,
    ):
        result = check_capability(capability, {capability})
        assert not result.allowed
        assert "permanently denied" in result.reason


def test_unknown_or_malformed_capability_fails_closed():
    assert not check_capability("unknown-capability", {"unknown-capability"}).allowed
    assert not check_capability(None, ()).allowed


def test_browser_capability_is_registered_and_safe():
    from autonomous_agent.tool_registry import get_tool
    assert Capability.BROWSER.value == "browser"
    assert get_tool("browser.open") is not None
    decision = authorize_tool("browser.open", [Capability.BROWSER])
    assert decision.allowed
