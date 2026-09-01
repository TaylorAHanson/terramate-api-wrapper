"""Lakebase (managed Postgres) connectivity.

Architecture.md §8/§10: the app has no durable local disk, so all state lives
in Lakebase, and the app authenticates to it with OAuth rather than a static
password — a Databricks identity token stands in for the Postgres password and
expires in roughly an hour. `_creator` is called by SQLAlchemy every time it
opens a new physical connection, so it mints a fresh token per connection
rather than baking one into a long-lived connection string; the pool's
`pool_recycle` keeps connections from living long enough for that to matter.

Locally and in CI (Seam 1's "real test Lakebase"), `DATABASE_URL` bypasses all
of this and points straight at a plain Postgres instance.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from server.config import get_settings

# Lakebase-minted tokens live for roughly an hour; recycling connections well
# before that means a connection is never handed out on a token that has
# already expired.
_POOL_RECYCLE_SECONDS = 1500


def _fetch_lakebase_credential(instance_name: str) -> tuple[str, str]:
    """Mint a short-lived (Postgres user, OAuth token) pair via the Databricks SDK."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    user = w.current_user.me().user_name
    credential = w.database.generate_database_credential(
        request_id=str(uuid.uuid4()),
        instance_names=[instance_name],
    )
    return user, credential.token


def _connect() -> Any:
    import psycopg2

    settings = get_settings()
    if settings.database_url:
        # psycopg2 doesn't understand SQLAlchemy's driver-qualified
        # postgresql+psycopg2:// scheme, so parse with SQLAlchemy (which
        # handles percent-encoded credentials correctly) and connect with the
        # decoded components rather than string-munging the URL.
        url = make_url(settings.database_url)
        return psycopg2.connect(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            password=url.password,
        )

    if not settings.lakebase_instance_name:
        raise RuntimeError(
            "No DATABASE_URL and no LAKEBASE_INSTANCE_NAME set — cannot connect to Lakebase. "
            "Set DATABASE_URL for local/test, or configure the postgres app resource "
            "and LAKEBASE_INSTANCE_NAME for a deployed Databricks App."
        )

    user, token = _fetch_lakebase_credential(settings.lakebase_instance_name)
    return psycopg2.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_database,
        user=settings.pg_user or user,
        password=token,
        sslmode="require",
    )


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            "postgresql+psycopg2://",
            creator=_connect,
            pool_pre_ping=True,
            pool_recycle=_POOL_RECYCLE_SECONDS,
        )
    return _engine


def reset_engine() -> None:
    """Dispose of the cached engine so the next `get_engine()` rebuilds it.

    Used by tests that change DATABASE_URL/env between cases.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Session:
    return Session(bind=get_engine())
