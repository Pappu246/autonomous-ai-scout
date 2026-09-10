from autonomous_agent.patch_review import review_patch


def test_review_patch_accepts_small_safe_diff():
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print('old')
+print('new')
"""
    result = review_patch(diff)
    assert result.allowed
    assert result.files == ("app.py",)
    assert result.additions == 1
    assert result.deletions == 1
    assert len(result.patch_digest) == 64
    assert "not been applied" in result.reason


def test_review_patch_rejects_empty_diff():
    result = review_patch("  ")
    assert not result.allowed
    assert result.reason == "patch is empty"


def test_review_patch_rejects_forbidden_paths():
    diff = """diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1 @@
-old
+new
"""
    result = review_patch(diff)
    assert not result.allowed
    assert "forbidden path" in result.reason


def test_review_patch_rejects_too_many_files():
    diff = "\n".join(f"+++ b/file_{index}.txt" for index in range(21))
    result = review_patch(diff)
    assert not result.allowed
    assert "too many files" in result.reason
