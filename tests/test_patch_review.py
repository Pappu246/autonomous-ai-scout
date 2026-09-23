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

def test_review_patch_rejects_parent_traversal_path():
    diff = """diff --git a/../../outside.py b/../../outside.py
--- a/../../outside.py
+++ b/../../outside.py
@@ -1 +1 @@
-old
+new
"""
    result = review_patch(diff)
    assert not result.allowed
    assert "forbidden path" in result.reason


def test_review_patch_rejects_absolute_and_windows_paths():
    for path in ("/tmp/outside.py", "C:/outside.py", "C:\\outside.py"):
        diff = f"""diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
        result = review_patch(diff)
        assert not result.allowed
        assert "forbidden path" in result.reason


def test_review_patch_rejects_file_deletion_and_rename_artifacts():
    deletion = """diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-print(1)
"""
    rename = """diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
"""
    assert not review_patch(deletion).allowed
    assert not review_patch(rename).allowed
