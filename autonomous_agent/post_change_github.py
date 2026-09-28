"""Phase 7 — live, read-only GitHub evidence acquisition for Phase 6.

This module is the transport boundary.  It may perform authenticated GitHub
GETs through a deliberately narrow transport protocol, but it never mutates
GitHub.  The Phase 6 verifier remains pure and is the sole verdict authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Final, Mapping, Protocol

from .post_change_evidence import CommitObservation, PullRequestObservation
from .post_change_final import (
    CheckConclusion,
    CheckObservation,
    Phase6Evidence,
    TestAttestation,
)
from .post_change_snapshot import (
    SnapshotEntry,
    SnapshotFileStatus,
    SnapshotObservation,
)
from .post_change_verification import VerificationError, VerificationRequest

MAX_PAGES: Final[int] = 6
PAGE_SIZE: Final[int] = 100
MAX_RUNS: Final[int] = 512


class GitHubEvidenceError(RuntimeError):
    """Bounded read-side evidence acquisition failure."""


class GitHubEvidenceTransport(Protocol):
    def head_sha(self, repository: str, branch: str) -> str | None: ...

    def get(self, repository: str, pull_request: str) -> Mapping[str, Any]: ...

    def pull_request_files(
        self, repository: str, pull_request: str, *, page: int = 1, per_page: int = PAGE_SIZE
    ) -> list[Mapping[str, Any]]: ...

    def git_tree(self, repository: str, commit_sha: str, *, recursive: bool = True) -> Mapping[str, Any]: ...

    def read_file_bytes_at_ref(self, repository: str, path: str, ref: str) -> bytes: ...

    def workflow_runs(
        self, repository: str, head_sha: str, *, page: int = 1, per_page: int = PAGE_SIZE
    ) -> list[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class GitHubPostChangeEvidenceProvider:
    """Acquire Phase 6 evidence from GitHub using only read-side transport."""

    transport: GitHubEvidenceTransport

    def collect(self, request: VerificationRequest) -> Phase6Evidence:
        if not isinstance(request, VerificationRequest):
            raise VerificationError("request must be VerificationRequest")

        commit = self.read_commit_identity(request)
        pull_request = self.read_pull_request(request)
        snapshot = self.read_file_snapshot(request)
        tests = self.read_test_attestation(request)
        return Phase6Evidence(
            commit=commit,
            pull_request=pull_request,
            snapshot=snapshot,
            tests=tests,
        )

    def read_commit_identity(self, request: VerificationRequest) -> CommitObservation | None:
        try:
            current_sha = self.transport.head_sha(request.repository, request.expected_head_branch)
            if not current_sha:
                return None
            return CommitObservation(
                repository=request.repository,
                commit_sha=str(current_sha),
                head_branch=request.expected_head_branch,
                base_branch=request.expected_base_branch,
                head_present=True,
            )
        except Exception:
            return None

    def read_pull_request(self, request: VerificationRequest) -> PullRequestObservation | None:
        try:
            value = self.transport.get(request.repository, str(request.pull_request_number))
            head = value.get("head")
            base = value.get("base")
            if not isinstance(head, Mapping) or not isinstance(base, Mapping):
                return None
            head_repo = head.get("repo")
            head_repository = (
                head_repo.get("full_name")
                if isinstance(head_repo, Mapping)
                else None
            )
            number = value.get("number")
            state = value.get("state")
            draft = value.get("draft")
            merged = value.get("merged")
            head_ref = head.get("ref")
            head_sha = head.get("sha")
            base_ref = base.get("ref")
            if (
                not isinstance(number, int) or isinstance(number, bool)
                or not isinstance(state, str)
                or not isinstance(draft, bool)
                or not isinstance(merged, bool)
                or not isinstance(head_repository, str)
                or not isinstance(head_ref, str)
                or not isinstance(head_sha, str)
                or not isinstance(base_ref, str)
            ):
                return None
            return PullRequestObservation(
                repository=request.repository,
                number=number,
                state=state,
                draft=draft,
                merged=merged,
                head_repository=head_repository,
                head_branch=head_ref,
                base_branch=base_ref,
                head_commit_sha=head_sha,
                head_present=True,
            )
        except Exception:
            return None

    def read_file_snapshot(self, request: VerificationRequest) -> SnapshotObservation | None:
        try:
            pull_files, files_complete = self._read_all_pull_files(request)
            tree = self.transport.git_tree(
                request.repository,
                request.expected_commit_sha,
                recursive=True,
            )
            tree_entries = tree.get("tree")
            if not isinstance(tree_entries, list):
                return SnapshotObservation(
                    request.repository,
                    request.expected_commit_sha,
                    (),
                    complete=False,
                    truncated=True,
                )
            if bool(tree.get("truncated", False)):
                return SnapshotObservation(
                    request.repository,
                    request.expected_commit_sha,
                    (),
                    complete=False,
                    truncated=True,
                )

            tree_by_path: dict[str, Mapping[str, Any]] = {}
            for raw in tree_entries:
                if not isinstance(raw, Mapping):
                    continue
                path = raw.get("path")
                if isinstance(path, str):
                    tree_by_path[path] = raw

            entries: list[SnapshotEntry] = []
            complete = files_complete
            changed_count = len(pull_files)
            for item in pull_files:
                if not isinstance(item, Mapping):
                    complete = False
                    continue
                path = item.get("filename")
                status = str(item.get("status", "")).lower()
                if not isinstance(path, str) or not path:
                    complete = False
                    continue
                if status == "removed":
                    complete = False
                    continue

                meta = tree_by_path.get(path)
                if meta is None:
                    complete = False
                    continue

                mode = str(meta.get("mode", ""))
                tree_type = str(meta.get("type", ""))
                is_symlink = mode == "120000"
                is_submodule = mode == "160000" or tree_type == "commit"

                if is_submodule:
                    digest = hashlib.sha256(
                        str(meta.get("sha", "")).encode("utf-8")
                    ).hexdigest()
                    size = 0
                    is_binary = False
                else:
                    try:
                        raw_content = self.transport.read_file_bytes_at_ref(
                            request.repository, path, request.expected_commit_sha
                        )
                        size = len(raw_content)
                        is_binary = False
                        try:
                            raw_content.decode("utf-8")
                        except UnicodeDecodeError:
                            is_binary = True
                        digest = hashlib.sha256(raw_content).hexdigest()
                    except Exception:
                        complete = False
                        continue

                previous = item.get("previous_filename")
                previous_path = str(previous) if isinstance(previous, str) and previous else None
                if status in {"added", "copied"}:
                    file_status = SnapshotFileStatus.ADDED
                else:
                    file_status = SnapshotFileStatus.MODIFIED

                try:
                    entries.append(
                        SnapshotEntry(
                            path=path,
                            content_sha256=digest,
                            status=file_status,
                            size_bytes=size,
                            is_binary=is_binary,
                            is_symlink=is_symlink,
                            is_submodule=is_submodule,
                            previous_path=previous_path,
                        )
                    )
                except VerificationError:
                    complete = False

            return SnapshotObservation(
                repository=request.repository,
                commit_sha=request.expected_commit_sha,
                entries=tuple(entries),
                complete=complete,
                truncated=False,
                observed_file_count=changed_count if complete else None,
            )
        except Exception:
            return None

    def read_test_attestation(self, request: VerificationRequest) -> TestAttestation | None:
        try:
            runs, runs_complete = self._read_all_workflow_runs(request)
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("head_sha", "")).lower() != request.expected_commit_sha.lower():
                    continue
                name = run.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                grouped.setdefault(name.strip(), []).append(run)

            observations: list[CheckObservation] = []
            for name in sorted(grouped):
                group = grouped[name]
                completed = all(str(run.get("status", "")).lower() == "completed" for run in group)
                conclusions = [str(run.get("conclusion", "")).lower() for run in group]
                conclusion = self._aggregate_conclusion(conclusions)
                event = self._aggregate_event(
                    [str(run.get("event", "")).lower() for run in group]
                )
                workflow_name = name
                run_ids = [
                    int(run.get("id"))
                    for run in group
                    if isinstance(run.get("id"), int) and not isinstance(run.get("id"), bool)
                ]
                if not run_ids:
                    continue
                observations.append(
                    CheckObservation(
                        name=name,
                        repository=request.repository,
                        head_sha=request.expected_commit_sha,
                        event=event,
                        status="completed" if completed else "in_progress",
                        conclusion=conclusion,
                        run_id=max(run_ids),
                        completed=completed,
                        workflow_name=workflow_name,
                    )
                )

            return TestAttestation(
                repository=request.repository,
                commit_sha=request.expected_commit_sha,
                checks=tuple(observations),
                complete=runs_complete,
            )
        except Exception:
            return None

    def _read_all_pull_files(
        self, request: VerificationRequest
    ) -> tuple[list[Mapping[str, Any]], bool]:
        result: list[Mapping[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = self.transport.pull_request_files(
                request.repository,
                str(request.pull_request_number),
                page=page,
                per_page=PAGE_SIZE,
            )
            result.extend(batch)
            if len(batch) < PAGE_SIZE:
                return result, True
        return result, False

    def _read_all_workflow_runs(
        self, request: VerificationRequest
    ) -> tuple[list[Mapping[str, Any]], bool]:
        result: list[Mapping[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = self.transport.workflow_runs(
                request.repository,
                request.expected_commit_sha,
                page=page,
                per_page=PAGE_SIZE,
            )
            result.extend(batch)
            if len(batch) < PAGE_SIZE:
                return result, True
            if len(result) >= MAX_RUNS:
                return result[:MAX_RUNS], False
        return result, False

    def verify(
        self,
        request: VerificationRequest,
        *,
        snapshot_policy=None,
        test_policy=None,
    ):
        """Collect live evidence and run the pure Phase 6 verifier."""
        from .post_change_final import TestAttestationPolicy, verify_phase6
        from .post_change_snapshot import SnapshotPolicy

        evidence = self.collect(request)
        return verify_phase6(
            request,
            evidence,
            snapshot_policy=snapshot_policy or SnapshotPolicy(),
            test_policy=test_policy or TestAttestationPolicy(),
        )

    @classmethod
    def from_env(cls, *, config=None):
        from .github_api import GitHubApiClient
        return cls(GitHubApiClient(config))

    @staticmethod
    def _aggregate_conclusion(values: list[str]) -> CheckConclusion:
        normalized = set(values)
        if "failure" in normalized or "startup_failure" in normalized:
            return CheckConclusion.FAILURE
        if "timed_out" in normalized:
            return CheckConclusion.TIMED_OUT
        if "cancelled" in normalized:
            return CheckConclusion.CANCELLED
        if "action_required" in normalized:
            return CheckConclusion.ACTION_REQUIRED
        if "neutral" in normalized:
            return CheckConclusion.NEUTRAL
        if "skipped" in normalized:
            return CheckConclusion.SKIPPED
        if normalized == {"success"}:
            return CheckConclusion.SUCCESS
        return CheckConclusion.UNKNOWN

    @staticmethod
    def _aggregate_event(values: list[str]) -> str:
        normalized = set(values)
        if any(value not in {"push", "pull_request"} for value in normalized):
            for value in sorted(normalized):
                if value not in {"push", "pull_request"}:
                    return value or "unknown"
        if "pull_request" in normalized:
            return "pull_request"
        if "push" in normalized:
            return "push"
        return "unknown"
