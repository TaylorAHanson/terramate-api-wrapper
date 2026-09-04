"""Seam 1: drive real HTTP + a real test Lakebase, with only GitHubClient faked.

These are wiring smoke tests: the endpoints respond, the reconcile loop's
tick() is callable against a real database session (its actual request/Step
behavior is exercised in test_reconcile_loop.py, #19), and the GitHubClient
fake is a real substitution point future tests can assert against.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import orchestrator
from server.main import app
from tests.seam1_api.fakes import FakeGitHubClient

client = TestClient(app)


def test_health_endpoint_is_green():
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_endpoint_reports_build_info():
    response = client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "git_sha" in body


def test_tick_is_callable_against_a_real_test_lakebase(db_session):
    result = orchestrator.tick(db_session, FakeGitHubClient())
    assert result is None


def test_fake_github_client_records_opened_pull_requests():
    fake = FakeGitHubClient()

    ref = fake.open_pull_request(
        branch_name="provision/demo", base_branch="main", title="Add demo", body="body"
    )

    assert ref.number == 1
    assert len(fake.opened_pull_requests) == 1
    assert fake.opened_pull_requests[0].branch_name == "provision/demo"


def test_fake_github_client_numbers_prs_in_the_order_opened():
    """Per ADR-0004 the client only opens PRs — no status is faked; a Step's
    terminal outcome is driven through the `PUT .../outputs` ingress instead."""
    fake = FakeGitHubClient()
    first = fake.open_pull_request(branch_name="provision/a", base_branch="main", title="a", body="b")
    second = fake.open_pull_request(branch_name="provision/b", base_branch="main", title="b", body="b")

    assert (first.number, second.number) == (1, 2)
    assert [pr.branch_name for pr in fake.opened_pull_requests] == ["provision/a", "provision/b"]
