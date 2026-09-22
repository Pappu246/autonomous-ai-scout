from autonomous_agent.consequence_policy import ConsequenceAwareApprovalPolicy
from autonomous_agent.prompt_injection_guard import TrustLevel
from autonomous_agent.tool_registry import REGISTRY


def main() -> int:
    policy = ConsequenceAwareApprovalPolicy()
    print("N30 Consequence-Aware Approval demo")
    for name in ("filesystem.read", "filesystem.write", "email.send", "production.deploy"):
        tool = REGISTRY.get(name)
        decision = policy.evaluate(tool)
        print(f"{name}: mode={decision.mode.value}, consequence={decision.consequence.value}, reasons={decision.reasons}")
    untrusted = policy.evaluate(REGISTRY.get("filesystem.write"), origin_trust=TrustLevel.TOOL_RESULT)
    print(f"untrusted write: mode={untrusted.mode.value}, reasons={untrusted.reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
