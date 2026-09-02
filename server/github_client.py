"""The GitHub client seam (architecture.md §4.2, §8).

The orchestrator's single substitution point in tests: everything it needs
from GitHub — branch + commit bundle edits, open a PR, and read back
PR/checks/merge/apply status — goes through this interface. Per ADR-0002 the
real implementation polls for *status only* and never parses run logs or
reads Terraform state.

`RealGitHubClient` is the live implementation (#22, Seam 3): it commits
bundle edits via the Git Data API (blob -> tree -> commit -> ref, no local
checkout) and opens the PR, then reads back merge/close status from the PR
resource and the `terraform plan` text from a `terraform-plan` check run the
fixture repo's Actions workflow publishes (see
`fixtures/terraform-fixture-repo/.github/workflows/terraform.yml`) — never
Terraform state, never a run log.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence

import httpx
import yaml

from server.recipes.framework import AddFile, EditFile, FileEdit


@dataclass(frozen=True)
class PullRequestRef:
    number: int
    url: str


@dataclass(frozen=True)
class PullRequestStatus:
    merged: bool
    closed: bool


class GitHubClient(Protocol):
    def open_pull_request(
        self,
        *,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        edits: Sequence[FileEdit] = (),
    ) -> PullRequestRef: ...

    def get_pull_request_status(self, pr_number: int) -> PullRequestStatus: ...

    def get_plan(self, pr_number: int) -> str: ...


class GitHubClientError(RuntimeError):
    """A GitHub API call failed or returned something the client can't use."""


class PlanNotReadyError(GitHubClientError):
    """The fixture repo's plan check run hasn't reported a result yet."""


_PLAN_CHECK_RUN_NAME = "terraform-plan"


@dataclass
class RealGitHubClient:
    """Talks to the real GitHub REST + Git Data API for one repo.

    `repo` is `"owner/name"`. Commits bundle edits with a single blob -> tree
    -> commit -> ref sequence per PR (architecture.md §15.1's "commit path"),
    reading each `EditFile`'s current content from `base_branch` first so its
    `patch` mutates real repo content rather than a blank document.
    """

    repo: str
    token: str
    base_url: str = "https://api.github.com"
    # Bounded wait for the fixture repo's Actions plan job to publish the
    # `terraform-plan` check run (architecture.md §14: v1 has no live
    # check-polling loop yet, so this client absorbs the wait itself rather
    # than the orchestrator un-collapsing pr_open/planning/planned — see #19).
    plan_poll_timeout_seconds: float = 300.0
    plan_poll_interval_seconds: float = 5.0
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    @classmethod
    def from_settings(cls) -> "RealGitHubClient":
        """Build against the deployed Terramate repo (architecture.md §8):
        `GITHUB_PAT` + `GITHUB_REPO`, both env-injected — see server.config.
        """
        from server.config import get_settings

        settings = get_settings()
        if not settings.github_pat or not settings.github_repo:
            raise RuntimeError("GITHUB_PAT and GITHUB_REPO must both be set to build a RealGitHubClient")
        return cls(repo=settings.github_repo, token=settings.github_pat)

    # -- commit + PR -----------------------------------------------------

    def open_pull_request(
        self,
        *,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        edits: Sequence[FileEdit] = (),
    ) -> PullRequestRef:
        base_commit_sha = self._get_ref_sha(f"heads/{base_branch}")
        base_tree_sha = self._get(f"/repos/{self.repo}/git/commits/{base_commit_sha}")["tree"]["sha"]

        tree_entries = [
            {
                "path": edit.path,
                "mode": "100644",
                "type": "blob",
                "sha": self._create_blob(self._render_content(edit, base_branch)),
            }
            for edit in edits
        ]

        new_tree_sha = self._post(
            f"/repos/{self.repo}/git/trees",
            {"base_tree": base_tree_sha, "tree": tree_entries},
        )["sha"]
        new_commit_sha = self._post(
            f"/repos/{self.repo}/git/commits",
            {"message": title, "tree": new_tree_sha, "parents": [base_commit_sha]},
        )["sha"]
        self._post(
            f"/repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{branch_name}", "sha": new_commit_sha},
        )

        pr = self._post(
            f"/repos/{self.repo}/pulls",
            {"title": title, "head": branch_name, "base": base_branch, "body": body},
        )
        return PullRequestRef(number=pr["number"], url=pr["html_url"])

    def _render_content(self, edit: FileEdit, base_branch: str) -> str:
        if isinstance(edit, AddFile):
            return edit.content
        if isinstance(edit, EditFile):
            document = self._get_yaml_file(edit.path, ref=base_branch)
            return yaml.safe_dump(edit.patch(document), sort_keys=False)
        raise TypeError(f"Unknown FileEdit type: {type(edit)!r}")

    def _get_yaml_file(self, path: str, *, ref: str) -> dict:
        response = self._client.get(f"/repos/{self.repo}/contents/{path}", params={"ref": ref})
        if response.status_code == 404:
            return {}
        _raise_for_status(response)
        content = base64.b64decode(response.json()["content"]).decode("utf-8")
        return yaml.safe_load(content) or {}

    def _create_blob(self, content: str) -> str:
        return self._post(f"/repos/{self.repo}/git/blobs", {"content": content, "encoding": "utf-8"})["sha"]

    def _get_ref_sha(self, ref: str) -> str:
        return self._get(f"/repos/{self.repo}/git/ref/{ref}")["object"]["sha"]

    # -- status ------------------------------------------------------------

    def get_pull_request_status(self, pr_number: int) -> PullRequestStatus:
        pr = self._get(f"/repos/{self.repo}/pulls/{pr_number}")
        merged = bool(pr.get("merged")) or pr.get("merged_at") is not None
        closed = merged or pr.get("state") == "closed"
        return PullRequestStatus(merged=merged, closed=closed)

    def get_plan(self, pr_number: int) -> str:
        """Block until the fixture repo's plan check run reports a result.

        Never reads Terraform state or a run log (ADR-0002) — only the
        `terraform-plan` check run's `output.text` the Actions workflow
        publishes for this PR's head sha.
        """
        pr = self._get(f"/repos/{self.repo}/pulls/{pr_number}")
        head_sha = pr["head"]["sha"]

        deadline = time.monotonic() + self.plan_poll_timeout_seconds
        while True:
            run = self._find_plan_check_run(head_sha)
            if run is not None and run.get("status") == "completed":
                return (run.get("output") or {}).get("text") or ""
            if time.monotonic() >= deadline:
                raise PlanNotReadyError(
                    f"No completed '{_PLAN_CHECK_RUN_NAME}' check run for PR #{pr_number} "
                    f"after {self.plan_poll_timeout_seconds}s"
                )
            time.sleep(self.plan_poll_interval_seconds)

    def _find_plan_check_run(self, head_sha: str) -> dict | None:
        runs = self._get(f"/repos/{self.repo}/commits/{head_sha}/check-runs")["check_runs"]
        return next((r for r in runs if r["name"] == _PLAN_CHECK_RUN_NAME), None)

    # -- transport -----------------------------------------------------

    def _get(self, path: str) -> dict:
        response = self._client.get(path)
        _raise_for_status(response)
        return response.json()

    def _post(self, path: str, json_body: dict) -> dict:
        response = self._client.post(path, json=json_body)
        _raise_for_status(response)
        return response.json()


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        raise GitHubClientError(
            f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}"
        )
