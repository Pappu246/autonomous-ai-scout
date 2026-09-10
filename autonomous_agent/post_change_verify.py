from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PostChangeVerification:
    passed: bool
    checks: tuple[VerificationCheck, ...]
    reason: str


class VerificationBackend(Protocol):
    def get_head_commit(self, repository: str, branch: str) -> str: ...

    def get_pr_state(self, repository: str, pull_request: str) -> str: ...

    def run_tests(self, repository: str, branch: str) -> tuple[bool, str]: ...


def verify_post_change(
    repository: str,
    branch: str,
    expected_commit: str,
    pull_request: str,
    expected_files: Mapping[str, str],
    actual_files: Mapping[str, str],
    backend: VerificationBackend,
) -> PostChangeVerification:
    """Verify an already-created review change without merging, deploying, or changing it."""
    checks: list[VerificationCheck] = []

    head = backend.get_head_commit(repository, branch)
    checks.append(VerificationCheck("commit_identity", head == expected_commit, "head commit matches expected change" if head == expected_commit else "head commit changed unexpectedly"))

    state = backend.get_pr_state(repository, pull_request)
    pr_ok = state in {"draft", "open"}
    checks.append(VerificationCheck("pull_request_state", pr_ok, "PR remains reviewable" if pr_ok else f"unexpected PR state: {state}"))

    files_match = dict(expected_files) == dict(actual_files)
    checks.append(VerificationCheck("file_snapshot", files_match, "file snapshot matches expected contents" if files_match else "file snapshot differs from expected contents"))

    tests_ok, detail = backend.run_tests(repository, branch)
    checks.append(VerificationCheck("post_change_tests", tests_ok, detail))

    passed = all(check.passed for check in checks)
    return PostChangeVerification(
        passed,
        tuple(checks),
        "post-change verification passed" if passed else "post-change verification failed; merge/deploy must remain blocked",
    )
