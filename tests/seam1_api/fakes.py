"""The in-memory GitHubClient fake — Seam 1's single substitution point.

Records every pull request it was asked to open so a test can assert on order
and resolved content (architecture.md's testing decisions), without ever
touching real GitHub.

Per ADR-0004 the client only opens PRs — the API no longer polls GitHub for
status, so there is nothing else to fake. A Step's terminal outcome
(`done`/`failed`/`rejected`) is driven in tests by calling the `PUT
.../outputs` ingress (or `orchestrator.record_apply_result` directly), exactly
as CI would push it in production — not by a fake returning a merge/plan
status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from server.github_client import GitHubClient, PullRequestRef
from server.recipes.framework import FileEdit


@dataclass
class OpenedPullRequest:
    branch_name: str
    base_branch: str
    title: str
    body: str
    edits: Sequence[FileEdit] = field(default_factory=tuple)


@dataclass
class FakeGitHubClient(GitHubClient):
    opened_pull_requests: list[OpenedPullRequest] = field(default_factory=list)

    def open_pull_request(
        self,
        *,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        edits: Sequence[FileEdit] = (),
    ) -> PullRequestRef:
        self.opened_pull_requests.append(
            OpenedPullRequest(
                branch_name=branch_name, base_branch=base_branch, title=title, body=body, edits=edits
            )
        )
        number = len(self.opened_pull_requests)
        return PullRequestRef(number=number, url=f"https://github.com/example/repo/pull/{number}")
