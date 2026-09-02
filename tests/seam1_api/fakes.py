"""The in-memory GitHubClient fake — Seam 1's single substitution point.

Records every pull request it was asked to open so a test can assert on
order and resolved content (architecture.md's testing decisions), without
ever touching real GitHub.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from server.github_client import GitHubClient, PullRequestRef, PullRequestStatus
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
    """Merged and closed-unmerged ("rejected", architecture.md §6) are tracked
    as separate sets rather than one flag, since a test needs to simulate
    both outcomes independently.
    """

    opened_pull_requests: list[OpenedPullRequest] = field(default_factory=list)
    merged_pr_numbers: set[int] = field(default_factory=set)
    closed_unmerged_pr_numbers: set[int] = field(default_factory=set)

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

    def get_pull_request_status(self, pr_number: int) -> PullRequestStatus:
        merged = pr_number in self.merged_pr_numbers
        closed = merged or pr_number in self.closed_unmerged_pr_numbers
        return PullRequestStatus(merged=merged, closed=closed)

    def get_plan(self, pr_number: int) -> str:
        pr = self.opened_pull_requests[pr_number - 1]
        return (
            f"Terraform will perform the following actions:\n\n"
            f"  # {pr.title}\n  + create\n\n"
            f"Plan: 1 to add, 0 to change, 0 to destroy."
        )
