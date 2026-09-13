from autonomous_agent.capability_policy import Capability
from autonomous_agent.workflow_engine import WorkflowDefinition, WorkflowEngine


def test_workflow_engine_composes_registered_read_only_tools():
    workflow = WorkflowDefinition(
        name="research-and-browse",
        task="research a public topic and browse a page",
        tool_names=("web.search", "browser.open", "browser.extract"),
    )
    plan = WorkflowEngine().plan(
        workflow,
        granted=(Capability.WEB_RESEARCH, Capability.BROWSER),
    )
    assert plan.executable is True
    assert plan.risk.value == "medium"
    assert tuple(step.tool_name for step in plan.steps) == workflow.tool_names
    assert plan.audit.authorized is True


def test_workflow_engine_fails_closed_when_capability_is_not_granted():
    workflow = WorkflowDefinition(
        name="mail-read",
        task="read email",
        tool_names=("email.read",),
    )
    plan = WorkflowEngine().plan(workflow, granted=())
    assert plan.executable is False
    assert "email.read" in plan.reason


def test_workflow_engine_does_not_bypass_write_approval():
    workflow = WorkflowDefinition(
        name="write-file",
        task="write a file",
        tool_names=("filesystem.write",),
    )
    plan = WorkflowEngine().plan(
        workflow,
        granted=(Capability.FILES_WORKSPACE,),
    )
    assert plan.executable is False
    assert "explicit approval" in plan.reason
