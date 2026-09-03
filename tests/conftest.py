"""Shared fixtures.

Seam 1 (architecture.md's "HTTP API boundary, only GitHubClient faked") needs
a real test Lakebase behind it, not a mock — and the reconcile loop needs real
`FOR UPDATE SKIP LOCKED` row-locking semantics, which a mock or SQLite can't
give us. So the test session runs against a real Postgres:

- If `DATABASE_URL` is already set (CI's service container, or a developer's
  own Postgres), that wins and nothing is provisioned here.
- Otherwise, this boots an ephemeral embedded Postgres (real PostgreSQL,
  bundled binaries via `pgserver`) against a fresh temp data dir for the
  session, and tears it down at the end — no Docker, no service container, no
  system-installed Postgres required.

Either way, the real Alembic migrations run against it before any test
touches the database.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config

os.environ.setdefault("APP_ENVIRONMENT", "test")
os.environ.setdefault("ADMIN_PRINCIPALS", "admin-tester@example.com")
os.environ.setdefault("CI_PRINCIPALS", "ci-tester")

from server import database  # noqa: E402  (must follow the env default above)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EPHEMERAL_DB_NAME = "terramate_test"


def _boot_embedded_postgres() -> tuple[str, object]:
    """Start an ephemeral embedded Postgres and return (database_url, server).

    The caller is responsible for calling `server.cleanup()` at session end.
    """
    import pgserver

    data_dir = Path(tempfile.mkdtemp(prefix="terramate-test-pg-"))
    # cleanup_mode="delete" removes the data dir along with stopping the
    # server, so there's no leftover state to clean up ourselves.
    server = pgserver.get_server(data_dir, cleanup_mode="delete")

    conn = database.connect_to_url(server.get_uri())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE {_EPHEMERAL_DB_NAME}")
    finally:
        conn.close()

    return server.get_uri(database=_EPHEMERAL_DB_NAME), server


@pytest.fixture(scope="session")
def _database_url() -> Iterator[str]:
    existing = os.environ.get("DATABASE_URL")
    if existing:
        yield existing
        return

    url, server = _boot_embedded_postgres()
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        del os.environ["DATABASE_URL"]
        server.cleanup()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(_database_url: str):
    database.reset_engine()
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def db_session():
    session = database.get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
