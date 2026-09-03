"""Seam 1: `orchestrator.record_apply_result` — the persistence seam ADR-0003's
future `PUT /v1/requests/{id}/steps/{n}/outputs` endpoint will sit on (#54).

No HTTP here by design (the endpoint is #55) — these tests call the function
directly, against a real test Lakebase, driving the `workspace` Recipe's
`create` Step into `applying` the same way test_stuck_surfacing.py does.
"""
from __future__ import annotations

import logging
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


def _drive_create_into_applying(db_session, request_id: str) -> Step:
    """create's PR opens, is merged, and — with no output written yet — the
    Step parks at `applying` (mirrors test_stuck_surfacing.py)."""
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)  # create -> awaiting_approval
    create_pr = _step(_get_request(request_id), "create")["pr_number"]
    fake.merged_pr_numbers.add(create_pr)
    orchestrator.tick(db_session, fake)  # merged, output absent -> applying
    assert _step(_get_request(request_id), "create")["status"] == "applying"
    return db_session.scalars(
        select(Step).where(Step.request_id == request_id, Step.key == "create")
    ).one()


def _reload(db_session, step_id: str) -> Step:
    db_session.expire_all()
    return db_session.get(Step, step_id)


def test_a_successful_report_upserts_outputs_stores_console_and_advances_to_applied(db_session):
    request_id = _create_workspace_request("analytics")
    create_step = _drive_create_into_applying(db_session, request_id)

    orchestrator.record_apply_result(
        db_session,
        create_step,
        applied=True,
        outputs={"workspace_id": "ws-42"},
        tf_console="Apply complete! Resources: 1 added.",
    )

    step = _reload(db_session, create_step.id)
    assert step.status == "applied"
    assert step.tf_console == "Apply complete! Resources: 1 added."
    output = db_session.scalars(
        select(Output).where(Output.step_id == create_step.id, Output.key == "workspace_id")
    ).one()
    assert output.value == "ws-42"

    # The next Step's PR (bind) opens with create's output resolved by
    # reference — apply-result reporting is consumed downstream exactly as an
    # ADR-0002 direct write was.
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)
    detail = _get_request(request_id)
    assert _step(detail, "bind")["status"] == "awaiting_approval"
    assert "ws-42" in fake.opened_pull_requests[0].body


def test_a_duplicate_report_with_identical_values_is_a_no_op(db_session, caplog):
    request_id = _create_workspace_request("reporting")
    create_step = _drive_create_into_applying(db_session, request_id)

    orchestrator.record_apply_result(
        db_session, create_step, applied=True, outputs={"workspace_id": "ws-1"}, tf_console="first"
    )
    step = _reload(db_session, create_step.id)
    assert step.status == "applied"
    first_status_changed_at = step.status_changed_at

    with caplog.at_level(logging.INFO, logger="server.orchestrator"):
        orchestrator.record_apply_result(
            db_session, create_step, applied=True, outputs={"workspace_id": "ws-1"}, tf_console="first"
        )

    step = _reload(db_session, create_step.id)
    assert step.status == "applied"
    assert step.status_changed_at == first_status_changed_at
    outputs = db_session.scalars(
        select(Output).where(Output.step_id == create_step.id, Output.key == "workspace_id")
    ).all()
    assert len(outputs) == 1
    assert outputs[0].value == "ws-1"
    assert not any("step_transition" in r.message for r in caplog.records)


def test_applied_false_transitions_to_apply_failed(db_session):
    request_id = _create_workspace_request("finance")
    create_step = _drive_create_into_applying(db_session, request_id)

    orchestrator.record_apply_result(
        db_session, create_step, applied=False, outputs={}, tf_console="Error: something exploded"
    )

    step = _reload(db_session, create_step.id)
    assert step.status == "apply_failed"
    assert step.tf_console == "Error: something exploded"
    detail = _get_request(request_id)
    assert detail["status"] == "failed"


def test_a_partial_outputs_report_does_not_advance_the_step(db_session):
    request_id = _create_workspace_request("legal")
    create_step = _drive_create_into_applying(db_session, request_id)
    # Widen this Step's expected outputs beyond what the Recipe actually
    # produces, purely to exercise the partial-report path.
    create_step.produces = ["workspace_id", "workspace_url"]
    db_session.commit()

    orchestrator.record_apply_result(
        db_session, create_step, applied=True, outputs={"workspace_id": "ws-9"}, tf_console="partial"
    )

    step = _reload(db_session, create_step.id)
    assert step.status == "applying"
    assert step.tf_console == "partial"
    output = db_session.scalars(
        select(Output).where(Output.step_id == create_step.id, Output.key == "workspace_id")
    ).one()
    assert output.value == "ws-9"
