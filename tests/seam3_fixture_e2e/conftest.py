"""Seam 3 (#22): the real `GitHubClient` against the real throwaway fixture
repo (`fixtures/terraform-fixture-repo`, pushed as
`TaylorAHanson/terramate-fixture-repo` — see `fixtures/push_fixture_repo.sh`).

Unlike Seam 1/2, this drives real GitHub Actions runs (real wall-clock
minutes) and a Lakebase instance the fixture repo's own Action can reach
over the network — never picked up by a bare `pytest` (see
`pyproject.toml`'s `-m 'not seam3e2e'` default and each test's own
`pytest.mark.seam3e2e`). Every test here additionally self-skips if its
required env vars aren't set, so `pytest -m seam3e2e` degrades to a clear
skip reason rather than a wall of connection errors when run without live
credentials.

Required env vars:
  GITHUB_PAT          — a token with `repo` + `workflow` scope for the
                         fixture repo (the same one production would use).
  SEAM3_FIXTURE_REPO   — "owner/name", default TaylorAHanson/terramate-fixture-repo.
  SEAM3_DATABASE_URL   — a Postgres connection string *reachable from
                         GitHub-hosted Actions runners* (not localhost) —
                         this is also what the fixture repo's own
                         `SEAM3_DATABASE_URL` secret must point at, so the
                         Action's Lakebase write (ADR-0002) and this test's
                         own polling see the same `output` row.
"""
from __future__ import annotations

import os

import pytest

from server import database
from server.github_client import RealGitHubClient

SEAM3_FIXTURE_REPO = os.environ.get("SEAM3_FIXTURE_REPO", "TaylorAHanson/terramate-fixture-repo")

_missing = [
    name
    for name in ("GITHUB_PAT", "SEAM3_DATABASE_URL")
    if not os.environ.get(name)
]

skip_without_live_credentials = pytest.mark.skipif(
    bool(_missing),
    reason=f"Seam 3 needs {_missing} set (see tests/seam3_fixture_e2e/conftest.py)",
)


@pytest.fixture()
def seam3_db_session():
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = os.environ["SEAM3_DATABASE_URL"]
    database.reset_engine()
    try:
        session = database.get_session()
        try:
            yield session
        finally:
            session.close()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        database.reset_engine()


@pytest.fixture()
def real_github_client():
    client = RealGitHubClient(repo=SEAM3_FIXTURE_REPO, token=os.environ["GITHUB_PAT"])
    yield client
    client.close()
