"""Seam 1: trusted-identity resolution + admin gate authorization (#47) —
real HTTP + a real test Lakebase, no GitHubClient involved.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)

_ADMIN_HEADERS = {"X-Forwarded-Email": "admin-tester@example.com"}


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _create_request(headers: dict[str, str]):
    return client.post(
        "/v1/requests",
        headers={"Idempotency-Key": _idempotency_key(), **headers},
        json={"type": "schema", "params": {"catalog": "research", "name": "bronze", "owner": "data-eng"}},
    )


def test_requester_is_derived_from_the_forwarded_email():
    response = _create_request({"X-Forwarded-Email": "alice@example.com"})
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert client.get(f"/v1/requests/{request_id}").json()["requester"] == "alice@example.com"


def test_requester_falls_back_to_forwarded_user_when_no_email_is_forwarded():
    response = _create_request({"X-Forwarded-User": "svc-principal-123"})
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert client.get(f"/v1/requests/{request_id}").json()["requester"] == "svc-principal-123"


def test_a_spoofed_x_requester_header_is_ignored_and_the_request_is_rejected():
    response = _create_request({"X-Requester": "attacker@example.com"})
    assert response.status_code == 401


def test_a_spoofed_x_requester_never_overrides_the_forwarded_identity():
    response = _create_request(
        {"X-Forwarded-Email": "alice@example.com", "X-Requester": "attacker@example.com"}
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert client.get(f"/v1/requests/{request_id}").json()["requester"] == "alice@example.com"


def test_a_request_with_no_resolvable_identity_is_rejected():
    response = _create_request({})
    assert response.status_code == 401


def test_reading_the_intake_gate_without_a_forwarded_identity_is_rejected():
    response = client.get("/v1/admin/intake-gate")
    assert response.status_code == 401


def test_reading_the_intake_gate_as_a_non_admin_identity_is_forbidden():
    response = client.get("/v1/admin/intake-gate", headers={"X-Forwarded-Email": "not-an-admin@example.com"})
    assert response.status_code == 403


def test_reading_the_intake_gate_as_an_authorized_admin_succeeds():
    response = client.get("/v1/admin/intake-gate", headers=_ADMIN_HEADERS)
    assert response.status_code == 200


def test_setting_the_intake_gate_as_a_non_admin_identity_is_forbidden():
    response = client.post(
        "/v1/admin/intake-gate",
        json={"enabled": False},
        headers={"X-Forwarded-Email": "not-an-admin@example.com"},
    )
    assert response.status_code == 403


def test_setting_the_intake_gate_without_a_forwarded_identity_is_rejected():
    response = client.post("/v1/admin/intake-gate", json={"enabled": False})
    assert response.status_code == 401


def test_setting_the_intake_gate_as_an_authorized_admin_succeeds():
    response = client.post("/v1/admin/intake-gate", json={"enabled": True}, headers=_ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
