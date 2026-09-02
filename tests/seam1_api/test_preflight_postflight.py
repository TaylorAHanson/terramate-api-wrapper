"""Preflight/postflight hook invocation plumbing (#46): a `StepSpec`'s
`preflight` callables run before a Step's PR opens and `postflight` callables
run once its work completes, and a raising hook halts the Step. No concrete
Recipe wires non-empty hooks yet (#13 lands the checks themselves) — so these
tests inject hooks onto the `schema` Recipe for the duration of each test, to
exercise the invocation path itself.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Callable, Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from server import orchestrator
from server.database import get_session
from server.main import app
from server.models import Output, ProvisioningRequest, Step
from server.recipes.framework import Playbook
from server.recipes.registry import RECIPES
from server.recipes.schema import SchemaParams, SchemaRecipe
from tests.seam1_api.fakes import FakeGitHubClient

client = TestClient(app)


class _HookInjectingSchemaRecipe(SchemaRecipe):
    """Rebuilds the real `SchemaRecipe` Playbook, then attaches the given
    hooks onto its single Step — so `orchestrator._build_step_spec` (which
    rebuilds a Step's StepSpec from `RECIPES[type]` at claim time) re-derives
    a StepSpec with these hooks, exactly as a future Recipe with real
    preflight/postflight checks would (#13).
    """

    def __init__(
        self,
        preflight: Sequence[Callable[[], None]] = (),
        postflight: Sequence[Callable[[], None]] = (),
    ) -> None:
        self._preflight = preflight
        self._postflight = postflight

    def build(self, params: SchemaParams) -> Playbook:
        playbook = super().build(params)
        step = playbook.steps[0]
        return Playbook(
            steps=[replace(step, preflight=self._preflight, postflight=self._postflight)]
        )


@pytest.fixture(autouse=True)
def _clean_slate():
    """See `tests/seam1_api/test_reconcile_loop.py` — the reconcile loop
    claims every runnable queued Step globally, so this wipes any stray rows
    a prior run left in the shared test Lakebase.
    """
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


def _headers(idempotency_key: str) -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "X-Requester": "svc-tester"}


def _create_schema_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers=_headers(_idempotency_key()),
        json={"type": "schema", "params": {"catalog": "research", "name": name, "owner": "data-eng"}},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _get_request(request_id: str) -> dict:
    response = client.get(f"/v1/requests/{request_id}")
    assert response.status_code == 200
    return response.json()


def test_passing_preflight_and_postflight_advance_the_step_normally(db_session, monkeypatch):
    calls: list[str] = []
    monkeypatch.setitem(
        RECIPES,
        "schema",
        _HookInjectingSchemaRecipe(
            preflight=[lambda: calls.append("preflight")],
            postflight=[lambda: calls.append("postflight")],
        ),
    )
    request_id = _create_schema_request("bronze")
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)

    assert calls == ["preflight"]
    detail = _get_request(request_id)
    assert detail["steps"][0]["status"] == "awaiting_approval"
    assert len(fake.opened_pull_requests) == 1

    pr_number = detail["steps"][0]["pr_number"]
    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)

    assert calls == ["preflight", "postflight"]
    detail = _get_request(request_id)
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "applied"


def test_a_raising_preflight_blocks_pr_open_and_fails_the_step(db_session, monkeypatch):
    def _boom() -> None:
        raise RuntimeError("CIDR range unavailable")

    monkeypatch.setitem(RECIPES, "schema", _HookInjectingSchemaRecipe(preflight=[_boom]))
    request_id = _create_schema_request("silver")
    fake = FakeGitHubClient()

    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "failed"
    assert detail["steps"][0]["status"] == "plan_failed"
    assert detail["steps"][0]["pr_number"] is None
    assert fake.opened_pull_requests == []


def test_a_raising_postflight_fails_the_step_after_merge(db_session, monkeypatch):
    def _boom() -> None:
        raise RuntimeError("post-apply validation failed")

    monkeypatch.setitem(RECIPES, "schema", _HookInjectingSchemaRecipe(postflight=[_boom]))
    request_id = _create_schema_request("gold")
    fake = FakeGitHubClient()
    orchestrator.tick(db_session, fake)

    pr_number = _get_request(request_id)["steps"][0]["pr_number"]
    fake.merged_pr_numbers.add(pr_number)
    orchestrator.tick(db_session, fake)

    detail = _get_request(request_id)
    assert detail["status"] == "failed"
    assert detail["steps"][0]["status"] == "apply_failed"
    # The PR was already open before postflight ran — a raising postflight
    # halts the Step, but never retroactively un-opens its PR.
    assert len(fake.opened_pull_requests) == 1
