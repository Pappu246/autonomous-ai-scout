import pytest

from autonomous_agent.sandbox_patch import build_sandbox_patch


def test_patch_is_generated_without_being_applied():
    patch = build_sandbox_patch("app.py", "print('old')\n", "print('new')\n")
    assert patch.applied is False
    assert patch.path == "app.py"
    assert "-print('old')" in patch.diff
    assert "+print('new')" in patch.diff


def test_rejects_absolute_or_parent_paths():
    with pytest.raises(ValueError):
        build_sandbox_patch("/tmp/app.py", "a\n", "b\n")
    with pytest.raises(ValueError):
        build_sandbox_patch("src/../app.py", "a\n", "b\n")


def test_rejects_empty_patch():
    with pytest.raises(ValueError):
        build_sandbox_patch("app.py", "same\n", "same\n")
