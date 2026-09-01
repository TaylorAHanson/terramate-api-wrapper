"""Seam 1: `POST /v1/requests` + `GET /v1/requests/{id}` for the `schema`
type (#18) — real HTTP + a real test Lakebase, no GitHubClient involved
since no PR is opened by this ticket.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _headers(idempotency_key: str, requester: str = "svc-tester") -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "X-Requester": requester}


def test_openapi_publishes_the_schema_type_params_without_a_types_endpoint():
    spec = client.get("/openapi.json").json()
    assert "/v1/types" not in spec["paths"]

    request_schema = spec["components"]["schemas"]["SchemaProvisioningRequest"]
    assert request_schema["properties"]["type"]["const"] == "schema"
    assert "params" in request_schema["properties"]


def test_create_schema_request_persists_request_and_one_step():
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": "bronze", "owner": "data-eng"}},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["request_id"]

    detail_response = client.get(f"/v1/requests/{body['request_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["type"] == "schema"
    assert detail["requester"] == "svc-tester"
    assert detail["version"] == "v1"
    assert len(detail["steps"]) == 1
    assert detail["steps"][0]["key"] == "add-schema"
    assert detail["steps"][0]["status"] == "queued"
    assert detail["steps"][0]["pr_number"] is None


def test_resubmitting_the_same_idempotency_key_returns_the_original_request():
    key = _idempotency_key()
    payload = {"type": "schema", "params": {"catalog": "research", "name": "silver", "owner": "data-eng"}}

    first = client.post("/v1/requests", headers=_headers(key), json=payload)
    second = client.post("/v1/requests", headers=_headers(key), json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["request_id"] == second.json()["request_id"]

    request_id = first.json()["request_id"]
    steps = client.get(f"/v1/requests/{request_id}").json()["steps"]
    assert len(steps) == 1


def test_unknown_type_is_rejected_synchronously():
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "does-not-exist", "params": {}},
    )
    assert response.status_code == 422


def test_workspace_params_missing_required_fields_are_rejected_synchronously():
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "workspace", "params": {}},
    )
    assert response.status_code == 422


def test_params_failing_the_type_schema_are_rejected_synchronously():
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research"}},  # missing name/owner
    )
    assert response.status_code == 422


def test_get_unknown_request_returns_404():
    response = client.get(f"/v1/requests/{uuid.uuid4()}")
    assert response.status_code == 404
