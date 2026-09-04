"""Seam 1: the reconcile *driver* opens a request's PR on its own (#39, ADR-0004)
— real HTTP + a real test Lakebase, only GitHubClient faked, and crucially
**no manual `tick()` call**: the background loop is the motor here.

Per ADR-0004 opening PRs is the driver's only job — the terminal transition to
`done`/`succeeded` is a CI push, not something the loop polls for. So this
proves the loop actually calls `tick()` repeatedly (a Step reaches `submitted`
with no manual tick), a CI push then completes it, and the loop stops cleanly.
test_reconcile_loop.py already covers what one `tick()` does.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from server import orchestrator
from server.database import get_session
from server.main import app
from server.models import Output, ProvisioningRequest, Step
from server.scheduler import reconcile_loop
from tests.seam1_api.fakes import FakeGitHubClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_slate():
    """The reconcile loop claims every runnable queued Step globally, so wipe
    any stray rows a prior test left in the shared test Lakebase first (same
    reasoning as test_reconcile_loop.py's fixture)."""
    session = get_session()
    try:
        session.execute(delete(Output))
        session.execute(delete(Step))
        session.execute(delete(ProvisioningRequest))
        session.commit()
    finally:
        session.close()


def _create_schema_request(name: str) -> str:
    response = client.post(
        "/v1/requests",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Forwarded-Email": "svc-tester"},
        json={"type": "schema", "params": {"catalog": "research", "name": name, "owner": "data-eng"}},
    )
    assert response.status_code == 202
    return response.json()["request_id"]


def _read_request_sync(request_id: str) -> dict:
    """Read a request's rollup + first Step directly from the DB, off the event
    loop (psycopg2 is blocking) — deliberately not via HTTP, so the poll never
    contends with the loop task for the event-loop thread."""
    session = get_session()
    try:
        request = session.get(ProvisioningRequest, request_id)
        first_step = request.steps[0]
        return {"status": request.status, "step_status": first_step.status, "pr_number": first_step.pr_number}
    finally:
        session.close()


def _report_done_sync(request_id: str, ordinal: int = 0) -> None:
    """Simulate CI's `done` push, off the event loop — the driver never does
    this itself (ADR-0004); it only opens PRs."""
    session = get_session()
    try:
        step = session.scalars(
            select(Step).where(Step.request_id == request_id, Step.ordinal == ordinal)
        ).one()
        orchestrator.record_apply_result(session, step, outcome="done", outputs={}, tf_console="")
    finally:
        session.close()


async def _await(request_id: str, predicate, *, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    snapshot = await asyncio.to_thread(_read_request_sync, request_id)
    while not predicate(snapshot):
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting; last snapshot for {request_id}: {snapshot}")
        await asyncio.sleep(0.02)
        snapshot = await asyncio.to_thread(_read_request_sync, request_id)
    return snapshot


@pytest.mark.asyncio
async def test_driver_opens_the_pr_with_no_manual_tick_and_a_push_succeeds_it():
    request_id = _create_schema_request("bronze")
    fake = FakeGitHubClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        reconcile_loop(github_client=fake, interval_seconds=0.01, stop_event=stop)
    )
    try:
        # The driver opens the Step's PR on its own — no tick() called here.
        opened = await _await(request_id, lambda s: s["status"] == "in_progress")
        assert opened["step_status"] == "submitted"
        assert opened["pr_number"] == 1

        # CI's push completes it; the request rolls up to succeeded.
        await asyncio.to_thread(_report_done_sync, request_id)
        final = await _await(request_id, lambda s: s["status"] == "succeeded")
        assert final["step_status"] == "done"
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5.0)  # loop stops promptly on the event

    assert len(fake.opened_pull_requests) == 1  # motor didn't re-open the PR after submit


@pytest.mark.asyncio
async def test_a_failing_tick_does_not_kill_the_driver():
    """A tick that raises is logged and swallowed (run_tick_once), so the loop
    keeps running and recovers once the fault clears — the crash-safety the
    driver promises."""
    request_id = _create_schema_request("silver")

    class _FlakyGitHubClient(FakeGitHubClient):
        fail_next: bool = True

        def open_pull_request(self, **kwargs):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("simulated transient GitHub failure")
            return super().open_pull_request(**kwargs)

    fake = _FlakyGitHubClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        reconcile_loop(github_client=fake, interval_seconds=0.01, stop_event=stop)
    )
    try:
        # First tick raises inside open_pull_request; a later tick succeeds and
        # the Step still reaches submitted — the loop survived the fault.
        opened = await _await(request_id, lambda s: s["step_status"] == "submitted")
        assert opened["pr_number"] == 1
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5.0)
