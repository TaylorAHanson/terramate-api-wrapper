"""Seam 1: the `workspace` Recipe's 2-Step Playbook opens its PRs in
dependency order, and the `bind` Step's PR carries `create`'s apply-derived
`workspace_id` resolved by reference (#20) — real HTTP + a real test
Lakebase, with only GitHubClient faked.

There is no real GitHub Action here to write `create`'s output to Lakebase
(ADR-0002) — that lands with the fixture-repo integration, #22 — so these
tests simulate the Action's write by inserting the `Output` row directly,
the same way `test_reconcile_loop.py` fakes GitHub itself.
"""
from __future__ import annotations

import uuid

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
    return {"Idempotency-Key": idempotency_key, "X-Requester": requester}


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


def test_bind_pr_does_not_open_until_create_is_applied_and_its_output_is_captured(db_session):
    request_id = _create_workspace_request("reporting")
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert len(fake.opened_pull_requests) == 1
    assert _step(detail, "create")["status"] == "awaiting_approval"
    assert _step(detail, "bind")["status"] == "queued"
    create_pr_number = _step(detail, "create")["pr_number"]

    # Merge create's PR — but nothing has written its output yet.
    fake.merged_pr_numbers.add(create_pr_number)
    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "applying"
    assert _step(detail, "bind")["status"] == "queued"
    assert len(fake.opened_pull_requests) == 1  # bind held: no output yet

    # Simulate the GitHub Action's direct Lakebase write (ADR-0002).
    create_step_row = db_session.scalars(
        select(Step).where(Step.request_id == request_id, Step.key == "create")
    ).one()
    db_session.add(
        Output(id=str(uuid.uuid4()), step_id=create_step_row.id, key="workspace_id", value="ws-42")
    )
    db_session.commit()

    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "applied"
    assert _step(detail, "bind")["status"] == "awaiting_approval"
    assert len(fake.opened_pull_requests) == 2

    bind_pr = fake.opened_pull_requests[1]
    assert "ws-42" in bind_pr.body
    assert "steps.create.outputs.workspace_id" in bind_pr.body

    bind_pr_number = _step(detail, "bind")["pr_number"]
    fake.merged_pr_numbers.add(bind_pr_number)
    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert detail["status"] == "succeeded"
    assert _step(detail, "bind")["status"] == "applied"
