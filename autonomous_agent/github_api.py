from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class GitHubApiConfig:
    base_url: str = "https://api.github.com"
    token_env: str = "GITHUB_TOKEN"
    timeout_seconds: float = 20.0
    user_agent: str = "autonomous-ai-scout/0.2.3"

    @classmethod
    def from_env(cls) -> "GitHubApiConfig":
        base_url = os.getenv("GITHUB_API_URL", cls.base_url).strip().rstrip("/")
        token_env = os.getenv("GITHUB_TOKEN_ENV", cls.token_env).strip() or cls.token_env
        try:
            timeout = float(os.getenv("GITHUB_API_TIMEOUT_SECONDS", str(cls.timeout_seconds)))
        except ValueError:
            timeout = cls.timeout_seconds
        return cls(
            base_url=base_url or cls.base_url,
            token_env=token_env,
            timeout_seconds=max(1.0, min(timeout, 120.0)),
        )


class GitHubApiError(RuntimeError):
    pass


RequestFn = Callable[..., Mapping[str, Any]]


def _repo_path(repository: str) -> str:
    value = repository.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise GitHubApiError("repository must use owner/repository identity")
    owner, name = value.split("/", 1)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _branch_path(branch: str) -> str:
    value = branch.strip()
    if not value or value.startswith("/") or ".." in value or "\x00" in value:
        raise GitHubApiError("branch name is invalid")
    return quote(value, safe="")


def _file_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").removeprefix("./")
    drive_like = len(normalized) >= 2 and normalized[1] == ":"
    if (
        not normalized
        or normalized.startswith("/")
        or drive_like
        or normalized == ".."
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
        or normalized == ".git"
        or normalized.startswith(".git/")
        or normalized == ".github/workflows"
        or normalized.startswith(".github/workflows/")
        or normalized == ".env"
        or normalized.startswith(".env.")
        or normalized.startswith("state/secrets/")
    ):
        raise GitHubApiError("file path is outside the allowed GitHub change boundary")
    return normalized


def _pull_number(value: str) -> str:
    text = value.strip()
    match = re.search(r"/pull/(\d+)(?:/|$)", text)
    return match.group(1) if match else text.lstrip("#")


class GitHubApiClient:
    """Small GitHub REST adapter for the approved branch/commit/draft-PR boundary."""

    def __init__(
        self,
        config: GitHubApiConfig | None = None,
        *,
        http_request: Callable[..., Any] | None = None,
    ):
        self.config = config or GitHubApiConfig.from_env()
        self._http_request = http_request or self._default_request

    @classmethod
    def from_env(cls) -> "GitHubApiClient":
        return cls(GitHubApiConfig.from_env())

    def _headers(self) -> dict[str, str]:
        token = os.getenv(self.config.token_env, "").strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.config.user_agent,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _default_request(self, method: str, url: str, *, headers, params=None, json=None):
        with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True) as client:
            response = client.request(method, url, headers=headers, params=params, json=json)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def _request(self, method: str, path: str, *, params=None, payload=None) -> Any:
        token = os.getenv(self.config.token_env, "").strip()
        if not token:
            raise GitHubApiError(f"GitHub credential environment variable {self.config.token_env!r} is not configured")
        url = f"{self.config.base_url}{path}"
        try:
            result = self._http_request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=payload,
            )
        except Exception as exc:
            raise GitHubApiError(f"GitHub API request failed: {type(exc).__name__}") from exc
        return result

    def head_sha(self, repository: str, branch: str) -> str | None:
        try:
            result = self._request(
                "GET",
                f"{_repo_path(repository)}/branches/{_branch_path(branch)}",
            )
        except GitHubApiError:
            return None
        commit = result.get("commit")
        return str(commit.get("sha")) if isinstance(commit, Mapping) and commit.get("sha") else None

    def find(self, repository: str, head_branch: str, base_branch: str, patch_digest: str) -> str | None:
        del patch_digest  # Branch + base is a conservative duplicate boundary.
        owner = repository.strip().split("/", 1)[0]
        result = self._request(
            "GET",
            f"{_repo_path(repository)}/pulls",
            params={
                "state": "open",
                "head": f"{owner}:{head_branch}",
                "base": base_branch,
                "per_page": 100,
            },
        )
        items = result if isinstance(result, list) else result.get("items") if isinstance(result, Mapping) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, Mapping):
                    url = item.get("html_url") or item.get("url")
                    if url:
                        return str(url)
        return None

    def get(self, repository: str, pull_request: str) -> dict[str, Any]:
        result = self._request(
            "GET",
            f"{_repo_path(repository)}/pulls/{quote(_pull_number(pull_request), safe='')}",
        )
        return dict(result)

    def status(self, repository: str, pull_request: str) -> str:
        snapshot = self.get(repository, pull_request)
        head = snapshot.get("head")
        if not isinstance(head, Mapping) or not head.get("sha"):
            return "pending"
        result = self._request(
            "GET",
            f"{_repo_path(repository)}/commits/{quote(str(head['sha']), safe='')}/status",
        )
        state = str(result.get("state", "pending")).lower()
        if state in {"success", "failure", "error", "pending"}:
            return "failure" if state == "error" else state
        return "pending"

    def create_branch(self, repository: str, branch: str, base_branch: str) -> str:
        base_sha = self.head_sha(repository, base_branch)
        if not base_sha:
            raise GitHubApiError("target branch HEAD could not be verified")
        result = self._request(
            "POST",
            f"{_repo_path(repository)}/git/refs",
            payload={
                "ref": f"refs/heads/{branch.strip()}",
                "sha": base_sha,
            },
        )
        ref = result.get("ref")
        if not ref:
            raise GitHubApiError("GitHub did not return the created branch ref")
        return str(ref)

    def commit_files(
        self,
        repository: str,
        branch: str,
        files: Mapping[str, str],
        message: str,
    ) -> str:
        if not files:
            raise GitHubApiError("at least one file is required")
        normalized = {
            _file_path(path): str(content)
            for path, content in files.items()
        }
        if len(normalized) != len(files):
            raise GitHubApiError("duplicate normalized file paths are not allowed")

        branch_sha = self.head_sha(repository, branch)
        if not branch_sha:
            raise GitHubApiError("change branch HEAD could not be verified")

        commit_info = self._request(
            "GET",
            f"{_repo_path(repository)}/git/commits/{quote(branch_sha, safe='')}",
        )
        tree = commit_info.get("tree")
        base_tree = tree.get("sha") if isinstance(tree, Mapping) else None
        if not base_tree:
            raise GitHubApiError("base tree could not be resolved")

        entries = []
        for path, content in sorted(normalized.items()):
            blob = self._request(
                "POST",
                f"{_repo_path(repository)}/git/blobs",
                payload={
                    "content": content,
                    "encoding": "utf-8",
                },
            )
            blob_sha = blob.get("sha")
            if not blob_sha:
                raise GitHubApiError(f"GitHub did not return a blob SHA for {path}")
            entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": str(blob_sha),
                }
            )

        tree_result = self._request(
            "POST",
            f"{_repo_path(repository)}/git/trees",
            payload={
                "base_tree": str(base_tree),
                "tree": entries,
            },
        )
        tree_sha = tree_result.get("sha")
        if not tree_sha:
            raise GitHubApiError("GitHub did not return a tree SHA")

        commit_result = self._request(
            "POST",
            f"{_repo_path(repository)}/git/commits",
            payload={
                "message": message.strip() or "chore: prepare approved change",
                "tree": str(tree_sha),
                "parents": [branch_sha],
            },
        )
        commit_sha = commit_result.get("sha")
        if not commit_sha:
            raise GitHubApiError("GitHub did not return a commit SHA")

        self._request(
            "PATCH",
            f"{_repo_path(repository)}/git/refs/heads/{_branch_path(branch)}",
            payload={"sha": str(commit_sha), "force": False},
        )
        return str(commit_sha)

    def open_draft_pr(
        self,
        repository: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> str:
        result = self._request(
            "POST",
            f"{_repo_path(repository)}/pulls",
            payload={
                "title": title.strip(),
                "body": body.strip(),
                "head": head_branch.strip(),
                "base": base_branch.strip(),
                "draft": True,
            },
        )
        url = result.get("html_url") or result.get("url")
        if not url:
            raise GitHubApiError("GitHub did not return the created pull request")
        return str(url)
