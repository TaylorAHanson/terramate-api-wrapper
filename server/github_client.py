"""The GitHub client seam (architecture.md §4.2, §8).

The orchestrator's single substitution point in tests: everything it needs
from GitHub — branch + commit bundle edits, open a PR, and read back
PR/checks/merge/apply status — goes through this interface. Per ADR-0002 the
real implementation polls for *status only* and never parses run logs or
reads Terraform state.

No real implementation ships in this ticket (that lands with the live
fixture-repo integration, Seam 3). This module exists so the orchestrator can
be typed against it now and so tests have a stable interface to fake against.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        self, *, branch_name: str, base_branch: str, title: str, body: str
    ) -> PullRequestRef: ...

    def get_pull_request_status(self, pr_number: int) -> PullRequestStatus: ...
