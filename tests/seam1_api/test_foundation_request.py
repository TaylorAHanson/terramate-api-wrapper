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
    assert len(detail["steps"]) == 2
    step0 = detail["steps"][0]
    assert step0["key"] == "network_foundation"
    assert step0["depends_on"] == []
    assert step0["status"] == "queued"

    step1 = detail["steps"][1]
    assert step1["key"] == "business_domain"
    assert step1["depends_on"] == ["network_foundation"]
    assert step1["status"] == "queued"

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    # First tick opens Step 0 (network_foundation) PR
    assert len(fake.opened_pull_requests) == 1
    pr1 = fake.opened_pull_requests[0]
    assert pr1.title == "foundation: network_foundation"
    assert pr1.branch_name == f"provision/{request_id}/network_foundation"
    assert len(pr1.edits) == 1
    edit1 = pr1.edits[0]
    assert edit1.path == "src/configs/sbx/core_infrastructure/network_foundation/network_foundation.tm.yml"
    patched1 = edit1.patch(
        {"environments": {"sbx": {"inputs": {"business_domains": {"controltower": {"subnet_size": "small"}}}}}}
    )
    assert patched1["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"}
    }

    # Verify embedded PR metadata (HTML comment and JSON code block)
    import json
    import re
    meta_match = re.search(r"<!-- provisioning-metadata: (.*) -->", pr1.body)
    assert meta_match is not None
    meta = json.loads(meta_match.group(1))
    assert meta["request_id"] == request_id
    assert meta["ordinal"] == 0
    assert meta["step_key"] == "network_foundation"
    assert meta["type"] == "foundation"
    assert "```json:provisioning-metadata" in pr1.body

    # Step 0 is submitted, Step 1 is still queued
    detail = client.get(f"/v1/requests/{request_id}").json()
    assert detail["steps"][0]["status"] == "submitted"
    assert detail["steps"][1]["status"] == "queued"

    # Step 0 finishes apply in CI: report directly via step_key in URL (no ordinal lookup needed!)
    report0 = client.put(
        f"/v1/requests/{request_id}/steps/network_foundation/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}, "tf_console": "Step 0 Apply complete!"},
    )
    assert report0.status_code == 200

    # Second tick opens Step 1 (business_domain) PR now that Step 0 is done
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 2
    pr2 = fake.opened_pull_requests[1]
    assert pr2.title == "foundation: business_domain"
    assert pr2.branch_name == f"provision/{request_id}/business_domain"
    assert len(pr2.edits) == 1
    edit2 = pr2.edits[0]
    assert edit2.path == "src/configs/sbx/domain_stacks/business_domain/controltower/business_domain_controltower.tm.yml"

    # Verify Step 2 creates YAML when empty
    doc2 = edit2.patch({})
    assert doc2["metadata"]["name"] == "business_domain_controltower"
    assert doc2["environments"]["sbx"]["inputs"]["domain_name"] == "controltower"
    assert doc2["environments"]["sbx"]["inputs"]["network_foundation"] == "network_foundation"

    # Step 1 finishes apply in CI: report directly to /v1/requests/{request_id}/outputs!
    report1 = client.put(
        f"/v1/requests/{request_id}/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}, "tf_console": "Step 1 Apply complete!"},
    )
    assert report1.status_code == 200

    detail = client.get(f"/v1/requests/{request_id}").json()
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "done"
    assert detail["steps"][1]["status"] == "done"


def test_foundation_request_with_custom_business_domain(db_session):
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={
            "type": "foundation",
            "params": {
                "name": "prod-analytics",
                "business_domain": "wealth-management",
                "subnet_size": "medium",
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 1
    edit = fake.opened_pull_requests[0].edits[0]
    assert edit.path == "src/configs/sbx/core_infrastructure/network_foundation/network_foundation.tm.yml"
    patched = edit.patch(
        {"environments": {"sbx": {"inputs": {"business_domains": {"controltower": {"subnet_size": "small"}}}}}}
    )
    assert patched["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "wealth-management": {"subnet_size": "medium"},
    }

    # Complete Step 0, tick again for Step 1
    client.put(
        f"/v1/requests/{request_id}/steps/0/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}},
    )
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 2
    pr2 = fake.opened_pull_requests[1]
    assert pr2.title == "foundation: business_domain"
    edit2 = pr2.edits[0]
    assert edit2.path == "src/configs/sbx/domain_stacks/business_domain/wealth-management/business_domain_wealth-management.tm.yml"
    doc2 = edit2.patch({})
    assert doc2["metadata"]["name"] == "business_domain_wealth-management"
    assert doc2["environments"]["sbx"]["inputs"]["domain_name"] == "wealth-management"


def test_network_foundation_request_with_type_network_foundation(db_session):
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={
            "type": "network_foundation",
            "params": {
                "business_domain": "risk",
                "subnet_size": "large",
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 1
    pr = fake.opened_pull_requests[0]
    assert pr.title == "network_foundation: network_foundation"
    assert pr.branch_name == f"provision/{request_id}/network_foundation"
    edit = pr.edits[0]
    assert edit.path == "src/configs/sbx/core_infrastructure/network_foundation/network_foundation.tm.yml"
    patched = edit.patch(
        {"environments": {"sbx": {"inputs": {"business_domains": {"controltower": {"subnet_size": "small"}}}}}}
    )
    assert patched["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "risk": {"subnet_size": "large"},
    }

    # Complete Step 0 via step_key, tick again for Step 1
    client.put(
        f"/v1/requests/{request_id}/steps/network_foundation/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}},
    )
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 2
    pr2 = fake.opened_pull_requests[1]
    assert pr2.title == "network_foundation: business_domain"
    edit2 = pr2.edits[0]
    assert edit2.path == "src/configs/sbx/domain_stacks/business_domain/risk/business_domain_risk.tm.yml"
    doc2 = edit2.patch({})
    assert doc2["metadata"]["name"] == "business_domain_risk"
    assert doc2["environments"]["sbx"]["inputs"]["domain_name"] == "risk"

    # Complete Step 1 via direct request outputs endpoint with step_key
    res = client.put(
        f"/v1/requests/{request_id}/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "step_key": "business_domain", "outputs": {}},
    )
    assert res.status_code == 200
