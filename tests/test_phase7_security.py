from __future__ import annotations

import ast
from pathlib import Path

import autonomous_agent.post_change_github as phase7


def test_phase7_imports_only_local_phase6_transport_and_safe_stdlib():
    source = Path(phase7.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "hashlib",
        "dataclasses",
        "typing",
        "autonomous_agent.post_change_evidence",
        "autonomous_agent.post_change_final",
        "autonomous_agent.post_change_snapshot",
        "autonomous_agent.post_change_verification",
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in {"hashlib", "dataclasses", "typing"}
        elif isinstance(node, ast.ImportFrom):
            assert node.module
            if node.level:
                assert node.module in {
                    "post_change_evidence",
                    "post_change_final",
                    "post_change_snapshot",
                    "post_change_verification",
                }
            else:
                root = node.module.split(".")[0]
                assert root in {"__future__", "hashlib", "dataclasses", "typing", "autonomous_agent"}

def test_phase7_has_no_remote_mutation_calls():
    source = Path(phase7.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "commit_files",
        "open_draft_pr",
        "create_branch",
        "create_branch_at_sha",
        "delete_branch",
        "merge",
        "dispatch",
        "post",
        "patch",
        "put",
        "delete",
    }
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.add(node.func.id)
    assert calls.isdisjoint(forbidden)

def test_phase7_does_not_read_environment_directly():
    source = Path(phase7.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "os.getenv" not in source
    assert "GITHUB_TOKEN" not in source

def test_transport_protocol_is_read_only():
    names = {
        name
        for name in (
            "head_sha",
            "get",
            "pull_request_files",
            "git_tree",
            "read_file_bytes_at_ref",
            "workflow_runs",
        )
    }
    source = Path(phase7.__file__).read_text(encoding="utf-8")
    for name in names:
        assert f"def {name}(" in source
    assert "def merge(" not in source
    assert "def send(" not in source
    assert "def delete(" not in source
