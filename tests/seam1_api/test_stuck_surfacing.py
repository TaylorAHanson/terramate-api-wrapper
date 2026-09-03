"""Seam 1: a Step held at `applying` past the threshold is surfaced as `stuck`
(#43) — real HTTP + a real test Lakebase, only GitHubClient faked.

architecture.md §14 holds a merged Step at `applying` until its Action writes
the ADR-0002 outputs; if the Action never does, the Step is silent forever.
These tests drive the `workspace` Recipe's `create` Step into `applying` (merged,
but its `workspace_id` output not yet written), then prove the reconcile loop
flags it `stuck` once past the threshold, logs it exactly once, exposes it on
the API, and clears it when the output finally arrives.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

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
    """See test_reconcile_loop.py: tick() claims every runnable queued Step
    across the shared test Lakebase, so wipe stray rows first."""
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


def _create_workspace_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Forwarded-Email": "svc-tester"},
        json={
            "type": "workspace",
            "params": {"name": name, "metastore": "main", "domain_owner": "platform-team", "groups": ["data-eng"]},
        },
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _step(detail: dict, key: str) -> dict:
    return next(s for s in detail["steps"] if s["key"] == key)


def _get_request(request_id: str) -> dict:
    response = client.get(f"/v1/requests/{request_id}")
    assert response.status_code == 200
    return response.json()


def _drive_create_into_applying(db_session, request_id: str) -> None:
    """create's PR opens, is merged, and — with no output written yet — the
    Step parks at `applying` (the silent hold #43 makes visible)."""
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)  # create -> awaiting_approval
    create_pr = _step(_get_request(request_id), "create")["pr_number"]
    fake.merged_pr_numbers.add(create_pr)
    orchestrator.tick(db_session, fake)  # merged, output absent -> applying
    assert _step(_get_request(request_id), "create")["status"] == "applying"


def _age_status(db_session, request_id: str, key: str, seconds: float) -> None:
    step = db_session.scalars(
        select(Step).where(Step.request_id == request_id, Step.key == key)
    ).one()
    step.status_changed_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db_session.commit()


def _stuck_logs(caplog, request_id: str) -> list:
    return [
        r
        for r in caplog.records
        if r.name == "server.orchestrator"
        and r.message.startswith("step_stuck")
        and f"request_id={request_id}" in r.message
    ]


def test_a_step_held_at_applying_past_the_threshold_is_flagged_and_logged_once(db_session, caplog):
    request_id = _create_workspace_request("analytics")
    _drive_create_into_applying(db_session, request_id)
    _age_status(db_session, request_id, "create", seconds=7200)  # 2h — well past default 3600

    fake = FakeGitHubClient()
    with caplog.at_level(logging.WARNING, logger="server.orchestrator"):
        orchestrator.tick(db_session, fake)

    assert _step(_get_request(request_id), "create")["stuck"] is True
    logs = _stuck_logs(caplog, request_id)
    assert len(logs) == 1
    assert "status=applying" in logs[0].message and "held_for=" in logs[0].message

    # A second tick must NOT re-log it — the persisted flag is what stops the
    # ~15s driver spamming a warning every pass.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="server.orchestrator"):
        orchestrator.tick(db_session, fake)
    assert _stuck_logs(caplog, request_id) == []
    assert _step(_get_request(request_id), "create")["stuck"] is True


def test_a_freshly_applying_step_is_not_flagged(db_session):
    request_id = _create_workspace_request("reporting")
    _drive_create_into_applying(db_session, request_id)  # status_changed_at is ~now

    orchestrator.tick(db_session, FakeGitHubClient())  # default threshold (3600s)

    assert _step(_get_request(request_id), "create")["stuck"] is False


def test_stuck_flag_clears_when_the_output_finally_arrives(db_session):
    request_id = _create_workspace_request("finance")
    _drive_create_into_applying(db_session, request_id)
    _age_status(db_session, request_id, "create", seconds=7200)

    orchestrator.tick(db_session, FakeGitHubClient())
    assert _step(_get_request(request_id), "create")["stuck"] is True

    # The Action's ADR-0002 write finally lands; the next tick advances create
    # to `applied` and the stuck flag clears with the transition.
    create_row = db_session.scalars(
        select(Step).where(Step.request_id == request_id, Step.key == "create")
    ).one()
    db_session.add(Output(id=str(uuid.uuid4()), step_id=create_row.id, key="workspace_id", value="ws-42"))
    db_session.commit()

    orchestrator.tick(db_session, FakeGitHubClient())
    create_after = _step(_get_request(request_id), "create")
    assert create_after["status"] == "applied"
    assert create_after["stuck"] is False


def test_a_step_under_a_cancelled_request_is_not_flagged_stuck(db_session):
    """A halted request (#21) already stopped and the operator knows, so a Step
    left at `applying` under it is not surfaced as stuck-holding-for-a-human."""
    request_id = _create_workspace_request("legal")
    _drive_create_into_applying(db_session, request_id)
    _age_status(db_session, request_id, "create", seconds=7200)

    assert client.post(f"/v1/requests/{request_id}/cancel").status_code == 200
    orchestrator.tick(db_session, FakeGitHubClient())

    assert _step(_get_request(request_id), "create")["stuck"] is False
