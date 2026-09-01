"""Shared fixtures.

Seam 1 (architecture.md's "HTTP API boundary, only GitHubClient faked") needs
a real test Lakebase behind it, not a mock — so this points at an actual
Postgres via DATABASE_URL (a local/CI service container; see
.github/workflows/ci.yml) and runs the real Alembic migrations against it
before any test touches the database.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_test"
)
os.environ.setdefault("APP_ENVIRONMENT", "test")

from server import database  # noqa: E402  (must follow the env defaults above)

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
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
