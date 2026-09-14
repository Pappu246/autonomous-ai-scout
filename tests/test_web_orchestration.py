from autonomous_agent.task_orchestrator import TaskOrchestrator, OrchestrationState
from autonomous_agent.connector_registry import web_connector
from autonomous_agent.tool_registry import REGISTRY

def test_research_is_discoverable_without_network_calls_in_orchestrator():
    report=TaskOrchestrator(registry=REGISTRY,connector_registry=web_connector()).orchestrate("Research official information about X and compare three sources",granted=("web_research",))
    assert report.intent=="research"
    assert report.state is OrchestrationState.AUTHORIZED
    assert report.selected_tools==("web.search","web.read","web.extract","web.compare")

def test_research_requires_existing_policy_grant():
    report=TaskOrchestrator(registry=REGISTRY,connector_registry=web_connector()).orchestrate("Research X",granted=())
    assert report.state is OrchestrationState.BLOCKED

def test_orchestrator_does_not_execute_network_directly():
    connector=web_connector(); called=[]
    class Forbidden:
        def execute_authorized(self,*args): called.append(args); raise AssertionError("network must not be called by orchestrator")
    report=TaskOrchestrator(registry=REGISTRY,connector_registry=connector).orchestrate("Research X",granted=("web_research",))
    assert report.state is OrchestrationState.AUTHORIZED and called==[]
