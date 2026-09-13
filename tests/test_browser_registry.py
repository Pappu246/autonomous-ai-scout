from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import ApprovalRequirement, NetworkRequirement, REGISTRY


def test_browser_tools_are_registered_and_autonomous():
    for name in ("browser.open", "browser.click", "browser.extract"):
        tool = REGISTRY.get(name)
        assert tool is not None
        assert tool.capability == Capability.BROWSER.value
        assert tool.safe_autonomous is True
        assert tool.approval_requirement is ApprovalRequirement.NONE
        assert tool.network_requirement is NetworkRequirement.REQUIRED


def test_browser_tools_are_read_only():
    for name in ("browser.open", "browser.click", "browser.extract"):
        tool = REGISTRY.get(name)
        assert tool is not None
        assert tool.read_write_mode.value == "read_only"
