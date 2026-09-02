"""Seam 1: halt/cancel/off-switch — the sad paths and operator controls (#21)
— real HTTP + a real test Lakebase, with only GitHubClient faked.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from server import orchestrator
from server.database import get_session
from server.main import app
from server.models import Output, ProvisioningRequest, Step
from tests.seam1_api.fakes import FakeGitHubClient

client = TestClient(app)

_ADMIN_HEADERS = {"X-Forwarded-Email": "admin-tester@example.com"}


@pytest.fixture(autouse=True)
def _clean_slate():
    """`tick()` claims every runnable queued Step globally (see
    test_reconcile_loop.py's `_clean_slate`) — wipe stray rows from the
    shared test Lakebase before each test so this file's ticks only ever
    touch the requests it created.
    """
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _reset_intake_gate():
    """The intake gate is a single row shared across the whole test suite
    (and other local runs against the same Postgres) — always leave it open.
    """
    client.post("/v1/admin/intake-gate", json={"enabled": True}, headers=_ADMIN_HEADERS)
    yield
    client.post("/v1/admin/intake-gate", json={"enabled": True}, headers=_ADMIN_HEADERS)


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _headers(idempotency_key: str, requester: str = "svc-tester") -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "X-Forwarded-Email": requester}


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


def _insert_extra_queued_step(request_id: str, ordinal: int, key: str) -> None:
    """Direct-to-DB, bypassing the (currently single-Step) `schema` Recipe —
    the only way to put a second, independent Step on a request until a
    real multi-step Recipe ships.
    """
    session = get_session()
    try:
        session.add(
            Step(
                id=str(uuid.uuid4()),
                request_id=request_id,
                ordinal=ordinal,
                key=key,
                status="queued",
                depends_on=[],
            )
        )
        session.commit()
    finally:
        session.close()


# --- Cancel -----------------------------------------------------------------


def test_cancel_a_pending_request_halts_it_before_any_pr_opens(db_session):
    request_id = _create_schema_request("cancel-pending")

    response = client.post(f"/v1/requests/{request_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {"request_id": request_id, "status": "cancelled"}

    orchestrator.tick(db_session, FakeGitHubClient())

    detail = _get_request(request_id)
    assert detail["status"] == "cancelled"
    assert detail["steps"][0]["status"] == "queued"
    assert detail["steps"][0]["pr_number"] is None


def test_cancel_an_awaiting_approval_request_stops_it_from_being_merged_in(db_session):
    request_id = _create_schema_request("cancel-in-flight")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    pr_number = _get_request(request_id)["steps"][0]["pr_number"]

    response = client.post(f"/v1/requests/{request_id}/cancel")
    assert response.status_code == 200

    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "cancelled"
    assert detail["steps"][0]["status"] == "awaiting_approval"


def test_cancelling_an_already_cancelled_request_is_idempotent():
    request_id = _create_schema_request("cancel-twice")

    first = client.post(f"/v1/requests/{request_id}/cancel")
    second = client.post(f"/v1/requests/{request_id}/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"


def test_cancelling_a_succeeded_request_is_a_conflict(db_session):
    request_id = _create_schema_request("cancel-succeeded")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    pr_number = _get_request(request_id)["steps"][0]["pr_number"]
    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)
    assert _get_request(request_id)["status"] == "succeeded"

    response = client.post(f"/v1/requests/{request_id}/cancel")
    assert response.status_code == 409


def test_cancelling_a_failed_request_is_a_conflict(db_session):
    request_id = _create_schema_request("cancel-failed")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    pr_number = _get_request(request_id)["steps"][0]["pr_number"]
    fake.closed_unmerged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)
    assert _get_request(request_id)["status"] == "failed"

    response = client.post(f"/v1/requests/{request_id}/cancel")
    assert response.status_code == 409


def test_cancel_an_unknown_request_is_404():
    response = client.post(f"/v1/requests/{uuid.uuid4()}/cancel")
    assert response.status_code == 404


# --- Halt on failure (no auto-rollback, no further Steps) -------------------


def test_a_rejected_step_halts_the_request_without_rolling_back_or_advancing_siblings(db_session):
    request_id = _create_schema_request("halt-siblings")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    pr_number = _get_request(request_id)["steps"][0]["pr_number"]

    fake.closed_unmerged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)
    assert _get_request(request_id)["status"] == "failed"
    assert _get_request(request_id)["steps"][0]["status"] == "rejected"

    # A second, independent (no depends_on) Step queued after the halt must
    # never be claimed — the request stays halted even for Steps that never
    # depended on the one that failed it.
    _insert_extra_queued_step(request_id, ordinal=1, key="extra-step")
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "failed"
    extra_step = next(s for s in detail["steps"] if s["key"] == "extra-step")
    assert extra_step["status"] == "queued"
    assert extra_step["pr_number"] is None
    assert len(fake.opened_pull_requests) == 1


# --- Off-switch (intake gate) ------------------------------------------------


def test_intake_gate_defaults_to_open():
    response = client.get("/v1/admin/intake-gate", headers=_ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_closing_the_intake_gate_rejects_new_requests_but_lets_in_flight_work_drain(db_session):
    in_flight_request_id = _create_schema_request("drain-me")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    pr_number = _get_request(in_flight_request_id)["steps"][0]["pr_number"]

    close = client.post("/v1/admin/intake-gate", json={"enabled": False}, headers=_ADMIN_HEADERS)
    assert close.status_code == 200
    assert close.json()["enabled"] is False

    rejected = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": "new", "owner": "data-eng"}},
    )
    assert rejected.status_code == 503

    # Drain: the reconcile loop is untouched by the gate.
    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)
    assert _get_request(in_flight_request_id)["status"] == "succeeded"

    reopen = client.post("/v1/admin/intake-gate", json={"enabled": True}, headers=_ADMIN_HEADERS)
    assert reopen.status_code == 200
    assert reopen.json()["enabled"] is True

    accepted = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": "new", "owner": "data-eng"}},
    )
    assert accepted.status_code == 202


def test_idempotency_replay_succeeds_even_while_the_gate_is_closed():
    key = _idempotency_key()
    payload = {"type": "schema", "params": {"catalog": "research", "name": "replay", "owner": "data-eng"}}
    first = client.post("/v1/requests", headers=_headers(key), json=payload)
    assert first.status_code == 202

    client.post("/v1/admin/intake-gate", json={"enabled": False}, headers=_ADMIN_HEADERS)

    second = client.post("/v1/requests", headers=_headers(key), json=payload)
    assert second.status_code == 202
    assert second.json()["request_id"] == first.json()["request_id"]
