"""Seam 3 (#22): the real `GitHubClient`, driven against the real throwaway
fixture repo, proves the actual PR -> merge -> apply -> output-write -> poll
loop end to end — a `schema` request (single Step, no value-passing) and a
`workspace` request (two Steps, `create`'s apply-derived `workspace_id`
resolved by reference into `bind`'s real committed content).

Gated: see tests/seam3_fixture_e2e/conftest.py for required env vars and why
this never runs under a bare `pytest`.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from server import orchestrator
from server.main import app
from tests.seam3_fixture_e2e.conftest import SEAM3_FIXTURE_REPO, skip_without_live_credentials
from tests.seam3_fixture_e2e.gh_test_helpers import get_file_content, merge_pull_request

pytestmark = pytest.mark.seam3e2e

client = TestClient(app)

_TICK_TIMEOUT_SECONDS = 300
_TICK_INTERVAL_SECONDS = 5


def _headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4()), "X-Requester": "seam3-tester"}


def _get_request(request_id: str) -> dict:
    response = client.get(f"/v1/requests/{request_id}")
    assert response.status_code == 200
    return response.json()


def _step(detail: dict, key: str) -> dict:
    return next(s for s in detail["steps"] if s["key"] == key)


def _tick_until(session, github_client, request_id: str, predicate, timeout: float = _TICK_TIMEOUT_SECONDS) -> dict:
    """Advance the real loop, sleeping between passes so a real Actions run
    (plan or apply) has time to land — Seam 3's whole reason for existing
    over Seam 1's instant-fake ticks.
    """
    deadline = time.monotonic() + timeout
    detail = None
    while True:
        orchestrator.tick(session, github_client)
        detail = _get_request(request_id)
        if predicate(detail):
            return detail
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Request {request_id} did not reach the expected state within {timeout}s: {detail}"
            )
        time.sleep(_TICK_INTERVAL_SECONDS)


def _merge(pr_number: int) -> None:
    merge_pull_request(repo=SEAM3_FIXTURE_REPO, token=os.environ["GITHUB_PAT"], pr_number=pr_number)


@skip_without_live_credentials
def test_schema_request_reaches_succeeded_through_the_real_loop(seam3_db_session, real_github_client):
    response = client.post(
        "/v1/requests",
        headers=_headers(),
        json={
            "type": "schema",
            "params": {
                "catalog": "research",
                "name": f"seam3-{uuid.uuid4().hex[:8]}",
                "owner": "data-eng",
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    detail = _tick_until(
        seam3_db_session,
        real_github_client,
        request_id,
        lambda d: _step(d, "add-schema")["status"] == "awaiting_approval",
    )
    step = _step(detail, "add-schema")
    assert step["plan_ref"]  # RealGitHubClient.get_plan blocked until the real check run landed

    _merge(step["pr_number"])

    detail = _tick_until(
        seam3_db_session, real_github_client, request_id, lambda d: d["status"] == "succeeded"
    )
    assert _step(detail, "add-schema")["status"] == "applied"


@skip_without_live_credentials
def test_workspace_request_resolves_create_output_into_bind_pr(seam3_db_session, real_github_client):
    ws_name = f"seam3-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/v1/requests",
        headers=_headers(),
        json={
            "type": "workspace",
            "params": {"name": ws_name, "metastore": "main", "domain_owner": "platform-team", "groups": ["data-eng"]},
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    # `create`: opens, plans, merges, and — via the fixture repo's Action —
    # writes its apply-derived `workspace_id` to Lakebase (ADR-0002).
    detail = _tick_until(
        seam3_db_session,
        real_github_client,
        request_id,
        lambda d: _step(d, "create")["status"] == "awaiting_approval",
    )
    _merge(_step(detail, "create")["pr_number"])

    detail = _tick_until(
        seam3_db_session, real_github_client, request_id, lambda d: _step(d, "create")["status"] == "applied"
    )

    # `bind`: its PR should not open until `create`'s output landed, and its
    # real committed content (not just the PR body) should carry the
    # resolved value — never the raw placeholder token.
    detail = _tick_until(
        seam3_db_session,
        real_github_client,
        request_id,
        lambda d: _step(d, "bind")["status"] == "awaiting_approval",
    )
    bind_step = _step(detail, "bind")
    bindings_content = get_file_content(
        repo=SEAM3_FIXTURE_REPO,
        token=os.environ["GITHUB_PAT"],
        path="stacks/metastores/main/bindings.tm.yaml",
        ref=f"provision/{request_id}/bind",
    )
    assert "${steps.create.outputs.workspace_id}" not in bindings_content
    assert "workspace_id:" in bindings_content

    _merge(bind_step["pr_number"])

    detail = _tick_until(
        seam3_db_session, real_github_client, request_id, lambda d: d["status"] == "succeeded"
    )
    assert _step(detail, "bind")["status"] == "applied"
