"""Test-only GitHub helpers for Seam 3.

Merging a PR is deliberately **not** part of `GitHubClient` (architecture.md
§2: "a human reviewer approves and merges the PR" — the API only opens PRs
and reads status back). Seam 3 has no human in the loop, so these tests play
that role directly against the GitHub REST API, exactly the way a person
clicking "Merge" would.
"""
from __future__ import annotations

import base64

import httpx


def merge_pull_request(*, repo: str, token: str, pr_number: int) -> None:
    response = httpx.put(
        f"https://api.github.com/repos/{repo}/pulls/{pr_number}/merge",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"merge_method": "merge"},
        timeout=30.0,
    )
    response.raise_for_status()


def get_file_content(*, repo: str, token: str, path: str, ref: str) -> str:
    """Fetch a file's real content from a branch — used to prove a Step's PR
    committed actual resolved content (#22), not just a PR-body string.
    """
    response = httpx.get(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        params={"ref": ref},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30.0,
    )
    response.raise_for_status()
    return base64.b64decode(response.json()["content"]).decode("utf-8")
