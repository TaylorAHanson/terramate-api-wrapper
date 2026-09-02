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

Two resilience properties (#45, a #38 child):

- `get_plan` makes a single, immediate check for the `terraform-plan` check
  run rather than blocking/sleeping until one lands — `PlanNotReadyError`
  just means "not yet", and it's the *orchestrator*'s job (`_advance_pr_open`)
  to re-check on a later tick, so one Step's slow plan never stalls the tick
  that would otherwise claim and open PRs for other runnable Steps.
- The transport (`_get`/`_post`) retries a transient GitHub `5xx`/`429` with
  backoff (honoring `Retry-After` when GitHub sends it) before surfacing it as
  a failure; a non-transient `4xx` is never retried.
"""
from __future__ import annotations

import base64
import contextvars
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Sequence

import httpx
import yaml

from server.recipes.framework import AddFile, EditFile, FileEdit

logger = logging.getLogger(__name__)

# Correlating ids (request_id, step key/ordinal — #41's convention) for the
# transport-level retry/give-up log lines below, which otherwise have no way
# to know which Step's GitHub call they're retrying: the orchestrator sets
# this around each `GitHubClient` call it makes, scoped to that call only.
_correlation: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "github_client_correlation", default={}
)


@contextmanager
def correlate(*, request_id: str, step_key: str, ordinal: int) -> Iterator[None]:
    token = _correlation.set({"request_id": request_id, "step": step_key, "ordinal": ordinal})
    try:
        yield
    finally:
        _correlation.reset(token)


def _correlation_suffix() -> str:
    ctx = _correlation.get()
    if not ctx:
        return ""
    return " " + " ".join(f"{k}={v}" for k, v in ctx.items())


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
    # Bounded retry for a transient GitHub 5xx/429 (#45) — a non-transient 4xx
    # is never retried. `retry_backoff_max_seconds` caps exponential backoff;
    # a `Retry-After` header (e.g. on 429) always wins over it when present.
    max_retries: int = 4
    retry_backoff_base_seconds: float = 1.0
    retry_backoff_max_seconds: float = 30.0
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
        logger.info(
            "github_pr_opened repo=%s branch=%s pr_number=%s pr_url=%s",
            self.repo,
            branch_name,
            pr["number"],
            pr["html_url"],
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
        """Check once for the fixture repo's plan check run — never blocks.

        `PlanNotReadyError` means "not yet, try again on a later tick": the
        orchestrator's `_advance_pr_open` is what re-checks across ticks (#45),
        so a Step whose plan is slow to land never stalls the tick that opened
        its PR, and never stalls any other Step's tick.

        Never reads Terraform state or a run log (ADR-0002) — only the
        `terraform-plan` check run's `output.text` the Actions workflow
        publishes for this PR's head sha.
        """
        pr = self._get(f"/repos/{self.repo}/pulls/{pr_number}")
        head_sha = pr["head"]["sha"]
        run = self._find_plan_check_run(head_sha)
        if run is not None and run.get("status") == "completed":
            logger.info(
                "github_plan_ready repo=%s pr_number=%s head_sha=%s",
                self.repo,
                pr_number,
                head_sha,
            )
            return (run.get("output") or {}).get("text") or ""
        logger.debug(
            "github_plan_not_ready repo=%s pr_number=%s head_sha=%s",
            self.repo,
            pr_number,
            head_sha,
        )
        raise PlanNotReadyError(f"No completed '{_PLAN_CHECK_RUN_NAME}' check run yet for PR #{pr_number}")

    def _find_plan_check_run(self, head_sha: str) -> dict | None:
        runs = self._get(f"/repos/{self.repo}/commits/{head_sha}/check-runs")["check_runs"]
        return next((r for r in runs if r["name"] == _PLAN_CHECK_RUN_NAME), None)

    # -- transport -----------------------------------------------------

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, json_body: dict) -> dict:
        return self._request("POST", path, json_body)

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        attempt = 1
        while True:
            logger.debug("github_call method=%s path=%s attempt=%s", method, path, attempt)
            response = self._client.request(method, path, json=json_body)
            if not response.is_error:
                return response.json()
            if not _is_transient(response.status_code) or attempt > self.max_retries:
                if attempt > 1:
                    logger.error(
                        "github_retry_exhausted method=%s url=%s status=%s attempts=%s%s",
                        response.request.method,
                        response.request.url,
                        response.status_code,
                        attempt,
                        _correlation_suffix(),
                    )
                _raise_for_status(response)
            delay = _retry_delay(response, attempt, self.retry_backoff_base_seconds, self.retry_backoff_max_seconds)
            logger.warning(
                "github_retry method=%s url=%s status=%s attempt=%s delay=%.2fs%s",
                response.request.method,
                response.request.url,
                response.status_code,
                attempt,
                delay,
                _correlation_suffix(),
            )
            time.sleep(delay)
            attempt += 1


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_transient(status_code: int) -> bool:
    return status_code in _TRANSIENT_STATUS_CODES


def _retry_delay(response: httpx.Response, attempt: int, base_seconds: float, max_seconds: float) -> float:
    """`Retry-After` (GitHub sends it on `429`s, always as a second count for
    this API) wins when present; otherwise a capped exponential backoff."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return min(base_seconds * (2 ** (attempt - 1)), max_seconds)


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        # The GitHub token lives in the Authorization header, which is never
        # part of the request URL or the response text — so nothing logged here
        # leaks it (#41).
        logger.error(
            "github_call_failed method=%s url=%s status=%s",
            response.request.method,
            response.request.url,
            response.status_code,
        )
        raise GitHubClientError(
            f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}"
        )
