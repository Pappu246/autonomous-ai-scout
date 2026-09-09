from autonomous_agent.llm_planner import _approval_required, _extract_json


def test_extract_json_from_markdown_fence():
    value = _extract_json('```json\n{"summary":"ok","steps":["inspect"],"requires_approval":true}\n```')
    assert value["summary"] == "ok"
    assert value["requires_approval"] is True


def test_extract_json_plain():
    value = _extract_json('{"summary":"ok","steps":[]}')
    assert value["summary"] == "ok"


def test_approval_policy_flags_source_changes_even_if_model_would_allow_them():
    assert _approval_required("add a feature", ["inspect files", "change source code"])


def test_approval_policy_allows_read_only_tasks():
    assert not _approval_required("analyze the repository", ["inspect files", "run tests"])
