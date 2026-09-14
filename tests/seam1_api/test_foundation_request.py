"""Seam 1: proves the customer Foundation stack step end to end through the HTTP API
and reconcile loop.
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


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _headers(idempotency_key: str, requester: str = "svc-tester") -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "X-Forwarded-Email": requester}


def test_foundation_request_opens_pr_with_correct_yaml_and_path(db_session):
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={
            "type": "foundation",
            "params": {
                "name": "sbx-test",
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    detail = client.get(f"/v1/requests/{request_id}").json()
    assert len(detail["steps"]) == 1
    step = detail["steps"][0]
    assert step["key"] == "foundation"
    assert step["depends_on"] == []
    assert step["status"] == "queued"

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 1
    pr = fake.opened_pull_requests[0]
    assert pr.title == "foundation: foundation"
    assert pr.branch_name == f"provision/{request_id}/foundation"
    assert len(pr.edits) == 1
    edit = pr.edits[0]
    assert edit.path == "src/configs/sbx-test/core_infrastructure/foundation/foundation.tm.yml"
    assert "apiVersion: terramate.io/cli/v1" in edit.content
    assert "kind: BundleInstance" in edit.content
    assert "name: foundation" in edit.content
    assert 'source: "/src/bundles/core_infrastructure/foundation"' in edit.content
    assert "sbx-test:" in edit.content
    assert '"controltower"' in edit.content

    # Advance to terminal: no outputs required
    report = client.put(
        f"/v1/requests/{request_id}/steps/0/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}, "tf_console": "Apply complete!"},
    )
    assert report.status_code == 200

    detail = client.get(f"/v1/requests/{request_id}").json()
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "done"


def test_foundation_request_with_custom_business_domain(db_session):
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={
            "type": "foundation",
            "params": {
                "name": "prod-analytics",
                "business_domain": "wealth-management",
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 1
    edit = fake.opened_pull_requests[0].edits[0]
    assert edit.path == "src/configs/prod-analytics/core_infrastructure/foundation/foundation.tm.yml"
    assert "prod-analytics:" in edit.content
    assert '"wealth-management"' in edit.content
    assert '"controltower"' not in edit.content
