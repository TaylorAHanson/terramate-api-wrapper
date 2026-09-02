"""Seam 1: the engine narrates its work (#41).

A deployed operator's only window into the reconcile loop is its logs, so the
orchestrator must emit a `step_transition` line for every status change, a
`pr_opened` line when it opens a PR, and a `request_rollup` line when a
request's rollup status moves — each carrying the correlating `request_id`.
Driven through `orchestrator.tick()` directly (like test_reconcile_loop.py),
with `caplog` asserting the lines, against a real test Lakebase + fake GitHub.
"""
from __future__ import annotations

import logging
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


@pytest.fixture(autouse=True)
def _clean_slate():
    """tick() claims every runnable queued Step globally, so wipe stray rows a
    prior test left in the shared test Lakebase first (same reasoning as
    test_reconcile_loop.py)."""
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


def _create_schema_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Requester": "svc-tester"},
        json={"type": "schema", "params": {"catalog": "research", "name": name, "owner": "data-eng"}},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _events_for(caplog, request_id: str) -> list[str]:
    """The orchestrator log messages that name this request_id — so a
    concurrently-cleaned or stray row's lines never bleed into the assertion."""
    return [
        r.message
        for r in caplog.records
        if r.name == "server.orchestrator" and f"request_id={request_id}" in r.message
    ]


def test_opening_a_pr_logs_pr_opened_and_the_queued_to_awaiting_transition(db_session, caplog):
    """(#45) `queued` now passes through the persisted `pr_open` sub-state on
    its way to `awaiting_approval` — both `step_transition` lines are logged,
    even though a fake's instant `get_plan` walks through both in one tick.
    """
    request_id = _create_schema_request("bronze")
    fake = FakeGitHubClient()

    with caplog.at_level(logging.INFO, logger="server.orchestrator"):
        orchestrator.tick(db_session, fake)

    messages = _events_for(caplog, request_id)
    assert any(m.startswith("pr_opened") and "pr_number=1" in m for m in messages)
    assert any(m.startswith("step_transition") and "from=queued to=pr_open" in m for m in messages)
    assert any(m.startswith("step_transition") and "from=pr_open to=awaiting_approval" in m for m in messages)
    assert any(m.startswith("request_rollup") and "to=awaiting_approval" in m for m in messages)


def test_a_merge_logs_the_applied_transition_and_the_succeeded_rollup(db_session, caplog):
    request_id = _create_schema_request("silver")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    fake.merged_pr_numbers.add(1)

    with caplog.at_level(logging.INFO, logger="server.orchestrator"):
        orchestrator.tick(db_session, fake)

    messages = _events_for(caplog, request_id)
    assert any(m.startswith("step_transition") and "to=applied" in m for m in messages)
    assert any(m.startswith("request_rollup") and "to=succeeded" in m for m in messages)


def test_a_rejection_logs_a_warning_and_the_failed_rollup(db_session, caplog):
    request_id = _create_schema_request("gold")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    fake.closed_unmerged_pr_numbers.add(1)

    with caplog.at_level(logging.INFO, logger="server.orchestrator"):
        orchestrator.tick(db_session, fake)

    messages = _events_for(caplog, request_id)
    assert any(m.startswith("step_rejected") for m in messages)
    assert any(m.startswith("request_rollup") and "to=failed" in m for m in messages)


# -- HTTP-route log lines -------------------------------------------------


def test_create_and_cancel_log_request_lifecycle(caplog):
    with caplog.at_level(logging.INFO, logger="server.routes.requests"):
        request_id = _create_schema_request("copper")
        client.post(f"/v1/requests/{request_id}/cancel")

    messages = [r.message for r in caplog.records if r.name == "server.routes.requests"]
    assert any(m.startswith("request_created") and f"request_id={request_id}" in m for m in messages)
    assert any(m.startswith("request_cancelled") and f"request_id={request_id}" in m for m in messages)


def test_flipping_the_intake_gate_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="server.routes.admin"):
        client.post("/v1/admin/intake-gate", json={"enabled": False})
        client.post("/v1/admin/intake-gate", json={"enabled": True})

    messages = [r.message for r in caplog.records if r.name == "server.routes.admin"]
    assert any(m.startswith("intake_gate_set") and "to=False" in m for m in messages)
    assert any(m.startswith("intake_gate_set") and "to=True" in m for m in messages)
