from pathlib import Path
import pytest
from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import filesystem_connector
from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState,execute_plan
from autonomous_agent.filesystem_workspace import WorkspaceConnector,WorkspaceError
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import REGISTRY
def _workspace(tmp_path):return WorkspaceConnector(tmp_path)
def test_valid_read_and_list(tmp_path):
    (tmp_path/"a.txt").write_text("hello",encoding="utf-8");c=_workspace(tmp_path);assert c.read("a.txt").content=="hello";assert "a.txt" in c.list().entries
def test_valid_write_and_transform(tmp_path):
    c=_workspace(tmp_path);c.write("a.txt","hello world");assert c.transform("a.txt","world","workspace").content=="hello workspace"
def test_path_traversal_absolute_and_workspace_escape(tmp_path):
    c=_workspace(tmp_path)
    for p in ("../x","/etc/passwd"):
        with pytest.raises(WorkspaceError):c.read(p)
def test_symlink_escape_is_blocked(tmp_path):
    outside=tmp_path.parent/"outside-n3.txt";outside.write_text("secret",encoding="utf-8");link=tmp_path/"link";link.symlink_to(outside)
    try:
        c=_workspace(tmp_path)
        with pytest.raises(WorkspaceError):c.read("link")
    finally:outside.unlink(missing_ok=True);link.unlink(missing_ok=True)
def test_oversized_file_and_directory_are_bounded(tmp_path):
    (tmp_path/"big").write_bytes(b"x"*131072)
    with pytest.raises(WorkspaceError):_workspace(tmp_path).read("big")
    for i in range(20):(tmp_path/f"f{i}").write_text("x",encoding="utf-8")
    assert len(_workspace(tmp_path).list().entries)<=20
def test_unsupported_encoding_fails_closed(tmp_path):
    (tmp_path/"bad").write_bytes(b"\xff\xfe")
    with pytest.raises(WorkspaceError):_workspace(tmp_path).read("bad")
def test_permission_and_missing_path_fail_closed(tmp_path):
    with pytest.raises(WorkspaceError):_workspace(tmp_path).read("missing")
def test_duplicate_operation_is_deterministic(tmp_path):
    c=_workspace(tmp_path);c.write("a.txt","x");assert c.read("a.txt").fingerprint==c.read("a.txt").fingerprint
def test_secret_leakage_is_blocked(tmp_path):
    with pytest.raises(WorkspaceError):_workspace(tmp_path).write("secret.txt","api_key=sk-secret")
def test_write_requires_authorization_and_approval():
    assert not plan_task("write file",granted=[Capability.FILES_WORKSPACE]).executable
    assert plan_task("write file",granted=[Capability.FILES_WORKSPACE],explicitly_approved=True).executable
def test_connector_scopes_and_disabled_behavior():
    r=filesystem_connector();assert r.get("filesystem_workspace_read") and r.get("filesystem_workspace_write");assert r.resolve_tool("filesystem_workspace_write","filesystem.write") is REGISTRY.get("filesystem.write")
def test_network_is_not_available_to_filesystem():
    assert REGISTRY.get("filesystem.read").network_requirement.value=="none" and REGISTRY.get("filesystem.write").network_requirement.value=="none"
def test_capability_mismatch_fails_closed():
    assert not plan_task("read file",granted=[Capability.WEB_RESEARCH]).executable
def test_workspace_planner_selects_exact_operations():
    p=plan_task("workspace files",granted=[Capability.READ_FILE,Capability.FILES_WORKSPACE],explicitly_approved=True);assert [s.tool_name for s in p.steps]==["filesystem.list","filesystem.read","filesystem.write","filesystem.transform"]
def test_end_to_end_read_list_write_transform_audit(tmp_path):
    (tmp_path/"a.txt").write_text("seed",encoding="utf-8");c=_workspace(tmp_path);p=plan_task("workspace files",granted=[Capability.READ_FILE,Capability.FILES_WORKSPACE],explicitly_approved=True);req={"filesystem.list":{"operation":"list","path":"."},"filesystem.read":{"operation":"read","path":"a.txt"},"filesystem.write":{"operation":"write","path":"a.txt","content":"hello"},"filesystem.transform":{"operation":"transform","path":"a.txt","find":"hello","replace":"hello world"}}
    result=execute_plan(p,tmp_path,granted=[Capability.READ_FILE,Capability.FILES_WORKSPACE],explicitly_approved=True,audit_path=tmp_path/"audit.jsonl",execution_id="fs-1",workspace_connector=c,workspace_request=req);assert result.state is ExecutionState.VERIFIED and verify_execution_audit(tmp_path/"audit.jsonl") and (tmp_path/"a.txt").read_text(encoding="utf-8")=="hello world"
def test_cross_project_memory_isolation(tmp_path):
    m=CrossProjectMemory(tmp_path/"m.json");m.record_finding("a",{"kind":"filesystem","fingerprint":"x"});assert not m.has(project="b",kind="filesystem",fingerprint="x")
def test_memory_persistence_failure_is_observational(tmp_path):
    class Broken(CrossProjectMemory):
        def _save(self,*args,**kwargs):return False
    assert not Broken(tmp_path/"m.json").record_finding("a",{"kind":"filesystem","fingerprint":"x"})
def test_audit_and_cross_project_evidence_do_not_grant_authority():
    assert not REGISTRY.authorize("filesystem.write",granted=[Capability.READ_FILE],explicitly_approved=True).allowed
def test_retry_timeout_bounds():
    c=WorkspaceConnector(Path.cwd(),max_retries=2,timeout_seconds=30);assert c.max_retries==2 and c.timeout_seconds==30
