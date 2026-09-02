"""`RealGitHubClient` (#22): the Git Data API commit/PR/status/plan sequence,
against a mocked GitHub API (respx) — no real network, no real repo.
"""
from __future__ import annotations

import base64
import logging

import httpx
import pytest
import respx

from server.github_client import GitHubClientError, PlanNotReadyError, RealGitHubClient, correlate
from server.recipes.framework import AddFile, EditFile

REPO = "acme/fixture-repo"
BASE_URL = "https://api.github.com"
TOKEN = "ghp_supersecrettoken"


@pytest.fixture()
def client():
    c = RealGitHubClient(repo=REPO, token="test-token", base_url=BASE_URL)
    yield c
    c.close()


@respx.mock
def test_open_pull_request_commits_an_add_file_via_git_data_api(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/git/commits/base-commit-sha").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
    )
    blob_route = respx.post(f"{BASE_URL}/repos/{REPO}/git/blobs").mock(
        return_value=httpx.Response(201, json={"sha": "new-blob-sha"})
    )
    tree_route = respx.post(f"{BASE_URL}/repos/{REPO}/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": "new-tree-sha"})
    )
    commit_route = respx.post(f"{BASE_URL}/repos/{REPO}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "new-commit-sha"})
    )
    ref_route = respx.post(f"{BASE_URL}/repos/{REPO}/git/refs").mock(
        return_value=httpx.Response(201, json={})
    )
    pr_route = respx.post(f"{BASE_URL}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(
            201, json={"number": 7, "html_url": f"https://github.com/{REPO}/pull/7"}
        )
    )

    ref = client.open_pull_request(
        branch_name="provision/req-1/create",
        base_branch="main",
        title="workspace: create",
        body="body text",
        edits=[AddFile("stacks/workspaces/analytics/stack.tm.hcl", 'stack "analytics" {}\n')],
    )

    assert ref.number == 7
    assert ref.url == f"https://github.com/{REPO}/pull/7"

    import json as _json

    blob_payload = _json.loads(blob_route.calls[0].request.content)
    assert blob_payload == {"content": 'stack "analytics" {}\n', "encoding": "utf-8"}

    tree_payload = _json.loads(tree_route.calls[0].request.content)
    assert tree_payload["base_tree"] == "base-tree-sha"
    assert tree_payload["tree"] == [
        {
            "path": "stacks/workspaces/analytics/stack.tm.hcl",
            "mode": "100644",
            "type": "blob",
            "sha": "new-blob-sha",
        }
    ]

    commit_payload = _json.loads(commit_route.calls[0].request.content)
    assert commit_payload == {
        "message": "workspace: create",
        "tree": "new-tree-sha",
        "parents": ["base-commit-sha"],
    }

    ref_payload = _json.loads(ref_route.calls[0].request.content)
    assert ref_payload == {"ref": "refs/heads/provision/req-1/create", "sha": "new-commit-sha"}

    pr_payload = _json.loads(pr_route.calls[0].request.content)
    assert pr_payload == {
        "title": "workspace: create",
        "head": "provision/req-1/create",
        "base": "main",
        "body": "body text",
    }


@respx.mock
def test_open_pull_request_applies_an_edit_file_patch_to_fetched_content(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/git/commits/base-commit-sha").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
    )
    existing_yaml = "bindings: []\n"
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/stacks/metastores/main/bindings.tm.yaml").mock(
        return_value=httpx.Response(
            200, json={"content": base64.b64encode(existing_yaml.encode()).decode()}
        )
    )
    blob_route = respx.post(f"{BASE_URL}/repos/{REPO}/git/blobs").mock(
        return_value=httpx.Response(201, json={"sha": "new-blob-sha"})
    )
    respx.post(f"{BASE_URL}/repos/{REPO}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t"}))
    respx.post(f"{BASE_URL}/repos/{REPO}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c"}))
    respx.post(f"{BASE_URL}/repos/{REPO}/git/refs").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{BASE_URL}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(201, json={"number": 8, "html_url": "https://x/pull/8"})
    )

    def patch(document):
        return {**document, "bindings": [*document["bindings"], {"workspace_id": "ws-42"}]}

    client.open_pull_request(
        branch_name="provision/req-1/bind",
        base_branch="main",
        title="workspace: bind",
        body="body",
        edits=[EditFile("stacks/metastores/main/bindings.tm.yaml", patch)],
    )

    import json as _json

    committed_content = _json.loads(blob_route.calls[0].request.content)["content"]
    assert "ws-42" in committed_content


@respx.mock
def test_get_pull_request_status_reports_merged(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"merged": True, "merged_at": "2026-01-01", "state": "closed"})
    )
    status = client.get_pull_request_status(9)
    assert status.merged is True
    assert status.closed is True


@respx.mock
def test_get_pull_request_status_reports_open(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"merged": False, "merged_at": None, "state": "open"})
    )
    status = client.get_pull_request_status(9)
    assert status.merged is False
    assert status.closed is False


@respx.mock
def test_get_pull_request_status_reports_closed_unmerged(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"merged": False, "merged_at": None, "state": "closed"})
    )
    status = client.get_pull_request_status(9)
    assert status.merged is False
    assert status.closed is True


@respx.mock
def test_get_plan_returns_the_terraform_plan_check_run_text(client):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "head-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/commits/head-sha/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"name": "other-check", "status": "completed", "output": {}},
                    {
                        "name": "terraform-plan",
                        "status": "completed",
                        "output": {"text": "Plan: 1 to add, 0 to change, 0 to destroy."},
                    },
                ]
            },
        )
    )

    plan = client.get_plan(9)
    assert plan == "Plan: 1 to add, 0 to change, 0 to destroy."


@respx.mock
def test_get_plan_raises_plan_not_ready_without_blocking_when_the_check_run_has_not_completed_yet(client):
    """(#45) A single, immediate check — never an internal sleep/poll loop.
    The orchestrator's `_advance_pr_open` is what re-checks across ticks."""
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "head-sha"}})
    )
    check_runs_route = respx.get(f"{BASE_URL}/repos/{REPO}/commits/head-sha/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )

    with pytest.raises(PlanNotReadyError):
        client.get_plan(9)

    assert check_runs_route.call_count == 1


@respx.mock
def test_a_failed_github_call_raises_github_client_error(client):
    route = respx.get(f"{BASE_URL}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GitHubClientError):
        client.open_pull_request(branch_name="b", base_branch="main", title="t", body="body")

    assert route.call_count == 1, "a non-transient 4xx (other than 429) must never be retried"


# -- observability (#41) --------------------------------------------------


@respx.mock
def test_a_failed_call_is_logged_at_error_without_leaking_the_token(caplog):
    """Every failure logs a `github_call_failed` line, and the PAT (which lives
    only in the Authorization header) must never appear in any log record.
    `max_retries=0` keeps this test about the terminal failure, not retries
    (see the retry-specific tests below for that)."""
    secret_client = RealGitHubClient(repo=REPO, token=TOKEN, base_url=BASE_URL, max_retries=0)
    respx.get(f"{BASE_URL}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(500, text="boom")
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="server.github_client"):
            with pytest.raises(GitHubClientError):
                secret_client.open_pull_request(branch_name="b", base_branch="main", title="t", body="body")
    finally:
        secret_client.close()

    assert any(r.message.startswith("github_call_failed") and r.levelno == logging.ERROR for r in caplog.records)
    assert TOKEN not in caplog.text


@respx.mock
def test_open_pull_request_logs_the_opened_pr(client, caplog):
    respx.get(f"{BASE_URL}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/git/commits/base-commit-sha").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
    )
    respx.post(f"{BASE_URL}/repos/{REPO}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t"}))
    respx.post(f"{BASE_URL}/repos/{REPO}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c"}))
    respx.post(f"{BASE_URL}/repos/{REPO}/git/refs").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{BASE_URL}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(201, json={"number": 7, "html_url": f"https://github.com/{REPO}/pull/7"})
    )

    with caplog.at_level(logging.INFO, logger="server.github_client"):
        client.open_pull_request(branch_name="provision/req-1/create", base_branch="main", title="t", body="b")

    opened = [r.message for r in caplog.records if r.message.startswith("github_pr_opened")]
    assert opened and "pr_number=7" in opened[0]


@respx.mock
def test_get_plan_logs_ready_on_success(client, caplog):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "head-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/commits/head-sha/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={"check_runs": [{"name": "terraform-plan", "status": "completed", "output": {"text": "ok"}}]},
        )
    )

    with caplog.at_level(logging.INFO, logger="server.github_client"):
        client.get_plan(9)

    events = {r.message.split()[0] for r in caplog.records}
    assert "github_plan_ready" in events


@respx.mock
def test_get_plan_logs_not_ready_at_debug(client, caplog):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "head-sha"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/commits/head-sha/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )

    with caplog.at_level(logging.DEBUG, logger="server.github_client"):
        with pytest.raises(PlanNotReadyError):
            client.get_plan(9)

    assert any(r.message.startswith("github_plan_not_ready") for r in caplog.records)


# -- transient retry / backoff (#45) ---------------------------------------


@respx.mock
def test_a_transient_5xx_then_success_recovers(client, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("server.github_client.time.sleep", sleeps.append)
    route = respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        side_effect=[
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json={"merged": True, "merged_at": "2026-01-01", "state": "closed"}),
        ]
    )

    status = client.get_pull_request_status(9)

    assert status.merged is True
    assert route.call_count == 2
    assert sleeps == [1.0]  # base backoff on the first retry


@respx.mock
def test_a_429_honors_the_retry_after_header_over_exponential_backoff(client, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("server.github_client.time.sleep", sleeps.append)
    route = respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, text="rate limited"),
            httpx.Response(200, json={"merged": False, "merged_at": None, "state": "open"}),
        ]
    )

    status = client.get_pull_request_status(9)

    assert status.merged is False
    assert route.call_count == 2
    assert sleeps == [7.0]


@respx.mock
def test_a_non_transient_4xx_other_than_429_is_never_retried(client, monkeypatch):
    monkeypatch.setattr("server.github_client.time.sleep", lambda _seconds: pytest.fail("must not sleep/retry"))
    route = respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(return_value=httpx.Response(422, text="unprocessable"))

    with pytest.raises(GitHubClientError):
        client.get_pull_request_status(9)

    assert route.call_count == 1


@respx.mock
def test_retry_exhaustion_surfaces_the_underlying_error(monkeypatch):
    monkeypatch.setattr("server.github_client.time.sleep", lambda _seconds: None)
    exhausting_client = RealGitHubClient(repo=REPO, token="test-token", base_url=BASE_URL, max_retries=2)
    route = respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(
        return_value=httpx.Response(503, text="still unavailable")
    )

    try:
        with pytest.raises(GitHubClientError):
            exhausting_client.get_pull_request_status(9)
    finally:
        exhausting_client.close()

    assert route.call_count == 1 + 2  # the initial attempt plus max_retries retries


@respx.mock
def test_retry_attempts_and_exhaustion_are_logged_with_correlating_ids(monkeypatch, caplog):
    monkeypatch.setattr("server.github_client.time.sleep", lambda _seconds: None)
    correlating_client = RealGitHubClient(repo=REPO, token="test-token", base_url=BASE_URL, max_retries=1)
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/9").mock(return_value=httpx.Response(503, text="down"))

    try:
        with caplog.at_level(logging.WARNING, logger="server.github_client"):
            with correlate(request_id="req-1", step_key="create", ordinal=0):
                with pytest.raises(GitHubClientError):
                    correlating_client.get_pull_request_status(9)
    finally:
        correlating_client.close()

    retry_lines = [r.message for r in caplog.records if r.message.startswith("github_retry ")]
    assert any(
        "request_id=req-1" in m and "step=create" in m and "ordinal=0" in m for m in retry_lines
    )
    exhausted_lines = [r.message for r in caplog.records if r.message.startswith("github_retry_exhausted")]
    assert any(
        "request_id=req-1" in m and "step=create" in m and "ordinal=0" in m for m in exhausted_lines
    )
