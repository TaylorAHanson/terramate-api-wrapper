"""Seam 1: the `workspace` Recipe's 2-Step Playbook opens its PRs in
dependency order, and the `bind` Step's PR carries `create`'s apply-derived
`workspace_id` resolved by reference (#20) — real HTTP + a real test
Lakebase, with only GitHubClient faked.

There is no real CI here to report `create`'s outcome over HTTP (ADR-0003/
ADR-0004) — that lands with the fixture-repo integration, #22 — so these tests
simulate CI's terminal push by driving the real `PUT .../steps/{n}/outputs`
ingress (#55) directly, the same way `test_reconcile_loop.py` fakes GitHub
itself. Per ADR-0004 the API no longer polls for merge/plan: a Step sits at
`submitted` until CI pushes `done`/`failed`/`rejected`.
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


@pytest.fixture(autouse=True)
def _clean_slate():
    """See test_reconcile_loop.py's fixture of the same name: tick() claims
    every runnable queued Step across the whole (shared, persistent) test
    Lakebase, so stray rows from an earlier local run would otherwise get
    claimed here too.
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
    return {"Idempotency-Key": idempotency_key, "X-Forwarded-Email": requester}


def _create_workspace_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={
            "type": "workspace",
            "params": {
                "name": name,
                "metastore": "main",
                "domain_owner": "platform-team",
                "groups": ["data-eng"],
            },
        },
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _get_request(request_id: str) -> dict:
    response = client.get(f"/v1/requests/{request_id}")
    assert response.status_code == 200
    return response.json()


def _step(detail: dict, key: str) -> dict:
    return next(s for s in detail["steps"] if s["key"] == key)


def test_create_workspace_request_persists_a_two_step_playbook_wired_by_row_id():
    request_id = _create_workspace_request("analytics")
    detail = _get_request(request_id)

    assert len(detail["steps"]) == 2
    create_step = _step(detail, "create")
    bind_step = _step(detail, "bind")
    assert create_step["depends_on"] == []
    # depends_on is persisted as the *create* Step's row id, not its key
    # (server.routes.requests translates StepSpec.depends_on keys at insert
    # time) — bind's single dependency is create's own row.
    assert len(bind_step["depends_on"]) == 1
    assert bind_step["depends_on"][0] != "create"


def test_bind_pr_does_not_open_until_create_is_done_and_its_output_is_captured(db_session):
    request_id = _create_workspace_request("reporting")
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert len(fake.opened_pull_requests) == 1
    assert _step(detail, "create")["status"] == "submitted"
    assert _step(detail, "bind")["status"] == "queued"

    # A tick before create's push must not open bind's PR — its dependency
    # isn't `done` yet.
    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "bind")["status"] == "queued"
    assert len(fake.opened_pull_requests) == 1  # bind held: create not done yet

    # Simulate CI's ADR-0004 terminal push for create (`done` + outputs).
    create_ordinal = _step(detail, "create")["ordinal"]
    report = client.put(
        f"/v1/requests/{request_id}/steps/{create_ordinal}/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {"workspace_id": "ws-42"}, "tf_console": "Apply complete!"},
    )
    assert report.status_code == 200

    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "done"
    assert _step(detail, "bind")["status"] == "submitted"
    assert len(fake.opened_pull_requests) == 2

    bind_pr = fake.opened_pull_requests[1]
    assert "ws-42" in bind_pr.body
    assert "steps.create.outputs.workspace_id" in bind_pr.body

    bind_ordinal = _step(detail, "bind")["ordinal"]
    report = client.put(
        f"/v1/requests/{request_id}/steps/{bind_ordinal}/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}, "tf_console": "Apply complete!"},
    )
    assert report.status_code == 200
    detail = _get_request(request_id)
    assert detail["status"] == "succeeded"
    assert _step(detail, "bind")["status"] == "done"
