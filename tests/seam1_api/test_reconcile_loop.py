"""Seam 1: the reconcile loop walks a single-step Playbook to `succeeded` (#19)
— real HTTP + a real test Lakebase, with only GitHubClient faked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from server import orchestrator
from server.database import get_session
from server.main import app
from server.models import Output, ProvisioningRequest, Step
from tests.seam1_api.fakes import FakeGitHubClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_slate():
    """The reconcile loop claims every runnable queued Step, globally — so
    unlike other Seam 1 tests (which just read back their own request_id),
    these need a table wiped clean of any stray rows a prior run left behind
    in the shared test Lakebase, or `tick()` here would also claim those.
    """
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _headers(idempotency_key: str, requester: str = "svc-tester") -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "X-Requester": requester}


def _create_schema_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": name, "owner": "data-eng"}},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _get_request(request_id: str) -> dict:
    response = client.get(f"/v1/requests/{request_id}")
    assert response.status_code == 200
    return response.json()


def test_tick_claims_a_queued_step_and_walks_it_to_awaiting_approval(db_session):
    request_id = _create_schema_request("bronze")
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "awaiting_approval"
    step = detail["steps"][0]
    assert step["status"] == "awaiting_approval"
    assert step["pr_number"] == 1
    assert step["pr_url"] == "https://github.com/example/repo/pull/1"
    assert len(fake.opened_pull_requests) == 1

    plan_response = client.get(f"/v1/requests/{request_id}/steps/0/plan")
    assert plan_response.status_code == 200
    assert plan_response.json()["plan"]


def test_tick_after_a_faked_merge_advances_the_step_to_applied_and_succeeds_the_request(db_session):
    request_id = _create_schema_request("silver")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    pr_number = _get_request(request_id)["steps"][0]["pr_number"]
    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "applied"


def test_tick_after_a_faked_rejection_fails_the_request(db_session):
    request_id = _create_schema_request("gold")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    pr_number = _get_request(request_id)["steps"][0]["pr_number"]
    fake.closed_unmerged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "failed"
    assert detail["steps"][0]["status"] == "rejected"


def test_a_stalled_claim_is_resumed_on_a_later_tick(db_session):
    """A crash between claiming a Step and finishing the reconcile pass leaves
    the Step's claimed_at/claimed_by set but its status still `queued` — the
    claim query gates on status, not the claim columns, so the next tick just
    re-claims and resumes it rather than getting stuck.
    """
    request_id = _create_schema_request("platinum")

    stalled_step = db_session.scalars(
        select(Step).where(Step.request_id == request_id)
    ).one()
    stalled_step.claimed_at = datetime.now(timezone.utc)
    stalled_step.claimed_by = "some-worker-that-crashed:1234"
    db_session.commit()

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["steps"][0]["status"] == "awaiting_approval"
    assert detail["steps"][0]["pr_number"] == 1


def test_plan_endpoint_returns_409_before_the_step_has_a_plan():
    request_id = _create_schema_request("copper")
    response = client.get(f"/v1/requests/{request_id}/steps/0/plan")
    assert response.status_code == 409


def test_plan_endpoint_404s_for_an_unknown_step_ordinal():
    request_id = _create_schema_request("iron")
    response = client.get(f"/v1/requests/{request_id}/steps/5/plan")
    assert response.status_code == 404
