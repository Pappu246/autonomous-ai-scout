from autonomous_agent.llm_planner import _extract_json


def test_extract_json_from_markdown_fence():
    value = _extract_json('```json\n{"summary":"ok","steps":["inspect"],"requires_approval":true}\n```')
    assert value["summary"] == "ok"
    assert value["requires_approval"] is True


def test_extract_json_plain():
    value = _extract_json('{"summary":"ok","steps":[]}')
    assert value["summary"] == "ok"
