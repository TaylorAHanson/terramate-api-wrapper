"""Seam 1: `GET /v1/requests/{id}/steps/{n}` step-detail route (#48).

architecture.md §11 lists this standalone route, but only the sub-route
`.../steps/{n}/plan` existed before this — there was no way to fetch a single
Step's detail without pulling the entire request-detail payload.
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


def _create_schema_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": name, "owner": "data-eng"}},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def test_step_detail_returns_the_same_fields_as_the_request_detail_steps():
    request_id = _create_schema_request("cobalt")

    detail_response = client.get(f"/v1/requests/{request_id}")
    assert detail_response.status_code == 200
    embedded_step = detail_response.json()["steps"][0]

    step_response = client.get(f"/v1/requests/{request_id}/steps/0")
    assert step_response.status_code == 200
    assert step_response.json() == embedded_step


def test_step_detail_404s_for_an_unknown_ordinal_on_a_known_request():
    request_id = _create_schema_request("nickel")
    response = client.get(f"/v1/requests/{request_id}/steps/5")
    assert response.status_code == 404


def test_step_detail_404s_for_an_unknown_request_id():
    response = client.get(f"/v1/requests/{uuid.uuid4()}/steps/0")
    assert response.status_code == 404
