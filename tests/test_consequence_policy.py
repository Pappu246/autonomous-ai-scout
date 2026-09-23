from autonomous_agent.consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy, Consequence
from autonomous_agent.prompt_injection_guard import TrustLevel
from autonomous_agent.tool_registry import REGISTRY


def test_low_risk_read_is_autonomous():
    tool = REGISTRY.get("filesystem.read")
    decision = ConsequenceAwareApprovalPolicy().evaluate(tool)
    assert decision.mode is ApprovalMode.AUTONOMOUS
    assert decision.consequence is Consequence.NONE


def test_high_risk_write_requires_approval():
    tool = REGISTRY.get("filesystem.write")
    decision = ConsequenceAwareApprovalPolicy().evaluate(tool)
    assert decision.mode is ApprovalMode.REQUIRE_APPROVAL
    assert decision.consequence is Consequence.HIGH
    approved = ConsequenceAwareApprovalPolicy().evaluate(tool, explicitly_approved=True)
    assert approved.mode is ApprovalMode.AUTONOMOUS


def test_critical_capability_requires_approval():
    tool = REGISTRY.get("production.deploy")
    decision = ConsequenceAwareApprovalPolicy().evaluate(tool)
    assert decision.mode is ApprovalMode.REQUIRE_APPROVAL
    assert decision.consequence is Consequence.CRITICAL


def test_untrusted_origin_adds_side_effect_consequence():
    tool = REGISTRY.get("email.send")
    decision = ConsequenceAwareApprovalPolicy().evaluate(tool, origin_trust=TrustLevel.TOOL_RESULT)
    assert decision.mode is ApprovalMode.REQUIRE_APPROVAL
    assert "untrusted origin for side effect" in decision.reasons


def test_universal_tool_layer_reports_consequence_policy_before_invoke():
    from autonomous_agent.digital_tool import ToolInvocation, UniversalDigitalToolLayer
    calls = []
    result = UniversalDigitalToolLayer().invoke(
        ToolInvocation("filesystem.write", {"path": "x", "content": "data"}, "now"),
        granted=["files_workspace"],
        invoker=lambda _: calls.append(1) or "written",
    )
    assert not result.success
    assert "consequence-aware policy" in result.error
    assert not calls


def test_medium_risk_read_only_is_autonomous():
    policy = ConsequenceAwareApprovalPolicy()
    decision = policy.evaluate(REGISTRY.get("web.search"))
    assert decision.mode is ApprovalMode.AUTONOMOUS
    assert decision.consequence is Consequence.MEDIUM


def test_safe_write_requires_explicit_approval():
    policy = ConsequenceAwareApprovalPolicy()
    from dataclasses import replace
    tool = replace(
        REGISTRY.get("filesystem.read"),
        name="test.safe.write",
        read_write_mode=ReadWriteMode.SAFE_WRITE,
        safe_autonomous=False,
        approval_requirement=ApprovalRequirement.NONE,
    )
    decision = policy.evaluate(tool)
    assert decision.mode is ApprovalMode.REQUIRE_APPROVAL
    approved = policy.evaluate(tool, explicitly_approved=True)
    assert approved.mode is ApprovalMode.AUTONOMOUS
