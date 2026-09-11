from __future__ import annotations

from typing import Any, Callable, Mapping

from .action_queue import PendingAction
from .github_changes import GitHubChangeRequest, build_change_request


class GitHubConnectorError(RuntimeError):
    pass


class GitHubDomainConnector:
    """Official GitHub REST read adapter plus approval-bound change preparation."""

    def __init__(self, fetch: Callable[[str, Mapping[str, Any] | None], Any]):
        self._fetch = fetch

    @staticmethod
    def _repo(repository: str) -> str:
        repository = repository.strip()
        if repository.count("/") != 1 or any(not part for part in repository.split("/")):
            raise GitHubConnectorError("invalid repository scope")
        return repository

    def repository(self, repository: str) -> Mapping[str, Any]:
        repository = self._repo(repository)
        value = self._fetch(f"/repos/{repository}", None)
        if not isinstance(value, Mapping) or value.get("full_name") != repository:
            raise GitHubConnectorError("repository identity could not be verified")
        return {"full_name": repository, "id": value.get("id"), "default_branch": value.get("default_branch"), "archived": value.get("archived"), "fork": value.get("fork")}

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]:
        repository = self._repo(repository)
        value = self._fetch(f"/repos/{repository}/pulls/{int(number)}", None)
        if not isinstance(value, Mapping):
            raise GitHubConnectorError("pull request unavailable")
        base = value.get("base") or {}
        head = value.get("head") or {}
        if (base.get("repo") or {}).get("full_name") != repository or (head.get("repo") or {}).get("full_name") != repository:
            raise GitHubConnectorError("pull request crosses repository scope")
        return {"number": int(number), "state": value.get("state"), "draft": value.get("draft"), "base_sha": base.get("sha"), "head_sha": head.get("sha"), "base_branch": base.get("ref"), "head_branch": head.get("ref")}

    def checks(self, repository: str, head_sha: str) -> tuple[Mapping[str, Any], ...]:
        repository = self._repo(repository)
        if len(head_sha) != 40:
            raise GitHubConnectorError("invalid commit scope")
        value = self._fetch(f"/repos/{repository}/commits/{head_sha}/check-runs", {"per_page": 100})
        checks = value.get("check_runs", []) if isinstance(value, Mapping) else []
        return tuple({"name": item.get("name"), "status": item.get("status"), "conclusion": item.get("conclusion"), "head_sha": item.get("head_sha")} for item in checks if isinstance(item, Mapping) and str(item.get("head_sha") or head_sha) == head_sha)

    def prepare_change(self, action: PendingAction, repository: str, base_branch: str, head_branch: str, title: str, body: str, unified_diff: str) -> GitHubChangeRequest:
        """Prepare metadata only; remote mutation remains behind existing approval-gated executor."""
        return build_change_request(action, self._repo(repository), base_branch, head_branch, title, body, unified_diff)
