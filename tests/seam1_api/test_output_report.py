"""Seam 1: `PUT /v1/requests/{id}/steps/{n}/outputs` (ADR-0003/ADR-0004, #55) —
the real HTTP ingress a Step's CI reports its terminal outcome to (`done` with
outputs, `failed`, or `rejected`). Real HTTP + a real test Lakebase; only
GitHubClient is faked (test_apply_result.py, #54, covers the persistence seam
this route delegates to directly, with no HTTP).
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

_CI_HEADERS = {"X-Forwarded-User": "ci-tester"}


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


def _ordinal(request_id: str, key: str) -> int:
    return _step(_get_request(request_id), key)["ordinal"]


def _report(request_id: str, ordinal: int, headers: dict | None = None, **body):
    return client.put(
        f"/v1/requests/{request_id}/steps/{ordinal}/outputs",
        headers=_CI_HEADERS if headers is None else headers,
        json={"status": "done", "outputs": {}, "tf_console": "", **body},
    )


def _drive_create_into_submitted(db_session, request_id: str) -> None:
    """create's PR opens and the Step parks at `submitted`, waiting for CI's
    terminal push (ADR-0004)."""
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)  # create -> submitted (PR opened)
    assert _step(_get_request(request_id), "create")["status"] == "submitted"


def test_an_unauthenticated_report_is_rejected_and_leaves_the_step_untouched(db_session):
    request_id = _create_workspace_request("analytics")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(request_id, ordinal, headers={}, outputs={"workspace_id": "ws-1"})

    assert response.status_code == 401
    assert _step(_get_request(request_id), "create")["status"] == "submitted"


def test_a_report_from_a_non_ci_principal_is_forbidden_and_leaves_the_step_untouched(db_session):
    request_id = _create_workspace_request("reporting")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(
        request_id, ordinal, headers={"X-Forwarded-User": "not-ci"}, outputs={"workspace_id": "ws-1"}
    )

    assert response.status_code == 403
    assert _step(_get_request(request_id), "create")["status"] == "submitted"


def test_unknown_request_id_is_404():
    response = _report(str(uuid.uuid4()), 0, outputs={"workspace_id": "ws-1"})
    assert response.status_code == 404


def test_unknown_ordinal_is_404(db_session):
    request_id = _create_workspace_request("legal")
    _drive_create_into_submitted(db_session, request_id)

    response = _report(request_id, 99, outputs={"workspace_id": "ws-1"})
    assert response.status_code == 404


def test_an_unknown_outcome_value_is_422(db_session):
    """The ingress only accepts `done`/`failed`/`rejected` (ADR-0004) — anything
    else fails body validation before it can touch a Step."""
    request_id = _create_workspace_request("audit")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(request_id, ordinal, status="applied", outputs={"workspace_id": "ws-1"})
    assert response.status_code == 422


def test_a_report_against_a_step_with_no_pr_yet_is_rejected(db_session):
    """`bind` hasn't even opened its PR yet (still `queued`) — a report
    against it doesn't match reality and must not silently write outputs/
    tf_console onto a Step nothing is waiting on."""
    request_id = _create_workspace_request("finance")
    _drive_create_into_submitted(db_session, request_id)
    bind_ordinal = _ordinal(request_id, "bind")
    assert _step(_get_request(request_id), "bind")["status"] == "queued"

    response = _report(request_id, bind_ordinal, outputs={"whatever": "1"}, tf_console="nope")

    assert response.status_code == 409
    assert _step(_get_request(request_id), "bind")["status"] == "queued"


def test_a_done_report_drives_the_step_to_done_and_is_consumed_downstream(db_session):
    request_id = _create_workspace_request("growth")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(
        request_id, ordinal, status="done", outputs={"workspace_id": "ws-42"}, tf_console="Apply complete!"
    )

    assert response.status_code == 200
    assert response.json() == {"ordinal": ordinal, "key": "create", "status": "done"}
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "done"

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "bind")["status"] == "submitted"
    assert "ws-42" in fake.opened_pull_requests[0].body


def test_a_failed_report_drives_the_step_to_failed(db_session):
    request_id = _create_workspace_request("ops")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(request_id, ordinal, status="failed", outputs={}, tf_console="Error: exploded")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "failed"
    assert detail["status"] == "failed"


def test_a_rejected_report_drives_the_step_to_rejected(db_session):
    """A PR closed without merging is pushed as `rejected` by the on-close CI
    job (ADR-0004) — no outputs or console needed."""
    request_id = _create_workspace_request("declined")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    response = _report(request_id, ordinal, status="rejected")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    detail = _get_request(request_id)
    assert _step(detail, "create")["status"] == "rejected"
    assert detail["status"] == "failed"


def test_a_duplicate_report_with_identical_values_is_a_no_op_success(db_session):
    request_id = _create_workspace_request("dupes")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    first = _report(request_id, ordinal, outputs={"workspace_id": "ws-7"}, tf_console="first")
    assert first.status_code == 200
    first_status_changed_at = _step(_get_request(request_id), "create")["status_changed_at"]

    second = _report(request_id, ordinal, outputs={"workspace_id": "ws-7"}, tf_console="first")
    assert second.status_code == 200
    assert second.json()["status"] == "done"

    detail = _get_request(request_id)
    assert _step(detail, "create")["status_changed_at"] == first_status_changed_at
    outputs = db_session.scalars(
        select(Output).where(Output.key == "workspace_id")
    ).all()
    assert len(outputs) == 1


def test_a_repeated_report_against_an_already_done_step_is_accepted_idempotently(db_session):
    request_id = _create_workspace_request("repeat")
    _drive_create_into_submitted(db_session, request_id)
    ordinal = _ordinal(request_id, "create")

    assert _report(request_id, ordinal, outputs={"workspace_id": "ws-3"}, tf_console="first").status_code == 200
    # A later report with an updated console (a retried CI step, say) against
    # the now-`done` Step is still accepted, not a 409.
    response = _report(request_id, ordinal, outputs={"workspace_id": "ws-3"}, tf_console="second")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert _step(_get_request(request_id), "create")["status"] == "done"
