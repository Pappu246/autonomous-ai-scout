from pathlib import Path

import pytest

from autonomous_agent.ai_coding_brain import GitRepositoryInspector


def test_repository_inspector_rejects_path_escape(tmp_path: Path):
    inspector = GitRepositoryInspector(lambda repo, path: "secret")
    with pytest.raises(ValueError, match="inspection boundary"):
        inspector.inspect("owner/repo", ["../secret.txt"])


def test_repository_inspector_rejects_protected_paths(tmp_path: Path):
    inspector = GitRepositoryInspector(lambda repo, path: "secret")
    with pytest.raises(ValueError, match="inspection boundary"):
        inspector.inspect("owner/repo", [".env"])


def test_repository_inspector_rejects_invalid_repository_identity():
    inspector = GitRepositoryInspector(lambda repo, path: "ok")
    with pytest.raises(ValueError, match="owner/repository"):
        inspector.inspect("../owner/repo", ["app.py"])
