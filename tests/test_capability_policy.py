from autonomous_agent.capability_policy import Capability, check_capability


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
