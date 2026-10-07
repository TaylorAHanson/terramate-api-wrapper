"""Seam 1: the serial lane (`Recipe.parallel = False`). Across every
non-parallel Type, at most one Step PR is open at a time, and requests take
turns FIFO by creation time — including between a multi-Step request's
Steps. Parallel Types (`schema`) are unaffected.
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
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


def _submit(type_: str, params: dict) -> str:
    response = client.post(
        "/v1/requests",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Forwarded-Email": "svc-tester"},
        json={"type": type_, "params": params},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _report(request_id: str, ordinal: int, status: str = "done") -> None:
    response = client.put(
        f"/v1/requests/{request_id}/steps/{ordinal}/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": status, "outputs": {}},
    )
    assert response.status_code == 200


def _opened(fake: FakeGitHubClient) -> list[str]:
    return [pr.title for pr in fake.opened_pull_requests]


def test_a_second_serial_request_waits_until_the_first_pr_is_done(db_session):
    first = _submit("workspace_folder", {"business_domain": "finance"})
    _submit("unity_catalog", {"business_domain": "finance", "catalog_suffixes": ["ai"]})
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)
    orchestrator.tick(db_session, fake)
    assert _opened(fake) == ["workspace_folder: workspace_folder"]

    _report(first, 0)
    orchestrator.tick(db_session, fake)
    assert _opened(fake) == ["workspace_folder: workspace_folder", "unity_catalog: unity_catalog"]


def test_an_older_multi_step_request_keeps_its_turn_between_steps(db_session):
    # `foundation` is the real workspace Recipe (Seam 1's conftest swaps a
    # legacy create/bind Recipe in under `workspace` itself).
    workspace = _submit("foundation", {"business_domains": ["finance"]})
    _submit("workspace_folder", {"business_domain": "finance"})
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)
    assert _opened(fake) == ["foundation: network_foundation"]

    _report(workspace, 0)
    orchestrator.tick(db_session, fake)
    assert _opened(fake) == ["foundation: network_foundation", "foundation: business_domain"]

    _report(workspace, 1)
    orchestrator.tick(db_session, fake)
    assert _opened(fake)[-1] == "workspace_folder: workspace_folder"


def test_requests_are_served_in_creation_order_not_by_type(db_session):
    first = _submit("unity_catalog_schema", {"business_domain": "finance", "catalog_suffix": "ai", "schemas": ["bronze"]})
    second = _submit("workspace_folder", {"business_domain": "finance"})
    _submit("unity_catalog", {"business_domain": "finance"})
    fake = FakeGitHubClient()

    for request_id in (first, second):
        orchestrator.tick(db_session, fake)
        _report(request_id, 0)
    orchestrator.tick(db_session, fake)

    assert _opened(fake) == [
        "unity_catalog_schema: unity_catalog_schema",
        "workspace_folder: workspace_folder",
        "unity_catalog: unity_catalog",
    ]


def test_a_parallel_type_opens_alongside_an_open_serial_pr(db_session):
    _submit("workspace_folder", {"business_domain": "finance"})
    _submit("unity_catalog", {"business_domain": "finance"})
    _submit("schema", {"catalog": "research", "name": "bronze", "owner": "data-eng"})
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)

    assert sorted(_opened(fake)) == ["schema: add-schema", "workspace_folder: workspace_folder"]


@pytest.mark.parametrize("halt", ["rejected", "cancel"])
def test_a_halted_request_releases_the_lane(db_session, halt):
    first = _submit("workspace_folder", {"business_domain": "finance"})
    _submit("unity_catalog", {"business_domain": "finance"})
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    if halt == "cancel":
        assert client.post(f"/v1/requests/{first}/cancel").status_code == 200
    else:
        _report(first, 0, status="rejected")
    orchestrator.tick(db_session, fake)

    assert _opened(fake) == ["workspace_folder: workspace_folder", "unity_catalog: unity_catalog"]
