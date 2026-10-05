"""Seam 1: the single-Step domain stack types (`workspace_folder`,
`unity_catalog`, `unity_catalog_schema`) end to end through the HTTP API and
reconcile loop — each opens one PR that writes its stack file and registers
the stack's id in the environment config, with one uuid shared by both.
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

_CONFIG = 'globals {\n  stack_ids = {\n    network_foundation = "nf"\n  }\n}\n'


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


def _submit(type_: str, params: dict):
    return client.post(
        "/v1/requests",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Forwarded-Email": "svc-tester"},
        json={"type": type_, "params": params},
    )


@pytest.mark.parametrize(
    ("type_", "params", "stack_path", "stack_key", "list_input", "list_value"),
    [
        (
            "workspace_folder",
            {"business_domain": "finance"},
            "src/configs/sbx/domain_stacks/business_domain/workspace_folder/finance/finance_workspace_folder.tm.yml",
            "finance_workspace_folder",
            "folder_names",
            "finance_assets/ai_apps",
        ),
        (
            "unity_catalog",
            {"business_domain": "finance", "catalog_suffixes": ["ai"]},
            "src/configs/sbx/domain_stacks/data_domain/unity_catalog/finance/finance_unity_catalog.tm.yml",
            "finance_unity_catalog",
            "catalog_suffix",
            "ai",
        ),
        (
            "unity_catalog_schema",
            {"business_domain": "finance", "catalog_suffix": "ai", "schemas": ["bronze"]},
            "src/configs/sbx/domain_stacks/data_domain/unity_catalog_schema/finance/finance_ai_sbx_unity_catalog_schema.tm.yml",
            "finance_ai_sbx_unity_catalog_schema",
            "schema_list",
            "bronze",
        ),
    ],
)
def test_a_domain_stack_request_opens_one_pr_with_the_stack_file_and_its_stack_id(
    db_session, type_, params, stack_path, stack_key, list_input, list_value
):
    response = _submit(type_, params)
    assert response.status_code == 202
    request_id = response.json()["request_id"]

    detail = client.get(f"/v1/requests/{request_id}").json()
    assert [s["key"] for s in detail["steps"]] == [type_]

    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    assert len(fake.opened_pull_requests) == 1
    pr = fake.opened_pull_requests[0]
    assert pr.title == f"{type_}: {type_}"
    stack_edit, id_edit = pr.edits
    assert stack_edit.path == stack_path
    assert id_edit.path == "src/configs/sbx/sbx_config.tm.hcl"

    doc = stack_edit.patch({})
    assert doc["metadata"]["name"] == stack_key
    assert list_value in doc["environments"]["sbx"]["inputs"][list_input]
    assert f'    {stack_key} = "{doc["metadata"]["uuid"]}"' in id_edit.patch(_CONFIG).splitlines()

    report = client.put(
        f"/v1/requests/{request_id}/steps/0/outputs",
        headers={"X-Forwarded-User": "ci-tester"},
        json={"status": "done", "outputs": {}},
    )
    assert report.status_code == 200
    assert client.get(f"/v1/requests/{request_id}").json()["status"] == "succeeded"


@pytest.mark.parametrize("type_", ["workspace_folder", "unity_catalog", "unity_catalog_schema"])
def test_a_domain_stack_request_without_a_business_domain_is_rejected(type_):
    assert _submit(type_, {}).status_code == 422
