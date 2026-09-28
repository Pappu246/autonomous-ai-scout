from pathlib import Path
import ast

def test_phase9_release_evidence_has_no_io_or_mutation_escape_hatches():
    root = Path("autonomous_agent/release_evidence.py")
    tree = ast.parse(root.read_text(encoding="utf-8"))
    blocked = {
        "subprocess", "socket", "requests", "httpx", "urllib", "smtplib",
        "os", "pathlib", "shutil",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in blocked
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in blocked
