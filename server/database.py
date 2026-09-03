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

import logging
import os
import threading
import uuid
from typing import Any, Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from server.config import get_settings

logger = logging.getLogger(__name__)

# Lakebase-minted tokens live for roughly an hour; recycling connections well
# before that means a connection is never handed out on a token that has
# already expired.
_POOL_RECYCLE_SECONDS = 1500


def _schema_for(env: str) -> str | None:
    """The schema this deployment's tables live in, or None to keep the default.

    On managed Lakebase the app's role can CONNECT + CREATE but does NOT own the
    `public` schema, so an unqualified `CREATE TABLE` fails with "permission
    denied for schema public" (Postgres 15+ dropped the default CREATE grant on
    `public`). The role *can* create — and then owns — its own schema, so we put
    every table in a dedicated per-environment schema and pin `search_path` to
    it: no owner intervention, no GRANTs, and dev/test/prod stay isolated on the
    one injected Lakebase database. Local/test take the DATABASE_URL path (no
    PGDATABASE) and keep `public`, which the connecting user owns there. Pattern
    mirrors the sc-command-center reference app; `APP_DB_SCHEMA` overrides it.
    """
    if not os.environ.get("PGDATABASE"):
        return None
    override = os.environ.get("APP_DB_SCHEMA", "").strip()
    if override:
        return override
    return env if env in ("dev", "test", "prod") else "app"


# `CREATE SCHEMA IF NOT EXISTS` only needs to run once per process, not on every
# pooled connection — search_path itself rides in the connection's startup
# packet for free. A restart re-checks.
_schema_ready: set[str] = set()
_schema_ready_lock = threading.Lock()


def _ensure_schema(conn: Any, schema: str) -> None:
    with _schema_ready_lock:
        if schema in _schema_ready:
            return
    cur = conn.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    conn.commit()
    cur.close()
    with _schema_ready_lock:
        _schema_ready.add(schema)


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


def connect_to_url(database_url: str) -> Any:
    """Open a psycopg2 connection to any SQLAlchemy-style Postgres URL.

    Shared by the app's own `_connect()` below and by anything else that
    needs to reach a plain Postgres by URL (e.g. tests booting an embedded
    instance) — both need the same driver-scheme parsing and unix-socket
    fallback, so this is the one place that does it.
    """
    import psycopg2

    # psycopg2 doesn't understand SQLAlchemy's driver-qualified
    # postgresql+psycopg2:// scheme, so parse with SQLAlchemy (which
    # handles percent-encoded credentials correctly) and connect with the
    # decoded components rather than string-munging the URL.
    url = make_url(database_url)
    # A unix-domain-socket URL (e.g. from an embedded/local Postgres) has no
    # network host — the socket directory instead rides in the `host` query
    # param (`postgresql://user@/db?host=/path/to/sockdir`), since a bare
    # `/path` can't sit in a URL's host position.
    host = url.host or url.query.get("host")
    return psycopg2.connect(
        host=host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
    )


def _connect() -> Any:
    settings = get_settings()
    if settings.database_url:
        return connect_to_url(settings.database_url)

    if not settings.lakebase_instance_name:
        raise RuntimeError(
            "No DATABASE_URL and no LAKEBASE_INSTANCE_NAME set — cannot connect to Lakebase. "
            "Set DATABASE_URL for local/test, or configure the postgres app resource "
            "and LAKEBASE_INSTANCE_NAME for a deployed Databricks App."
        )

    # Imported locally, mirroring connect_to_url — psycopg2 has no module-level
    # import in this file, and this Lakebase branch referenced it without one
    # (never exercised until a real deployed connection, since tests take the
    # DATABASE_URL path above).
    import psycopg2

    user, token = _fetch_lakebase_credential(settings.lakebase_instance_name)
    schema = _schema_for(settings.app_environment)
    connect_kwargs: dict[str, Any] = dict(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_database,
        user=settings.pg_user or user,
        password=token,
        sslmode="require",
    )
    if schema:
        # Sent in the startup packet so the session begins on the right
        # search_path with no extra round trip; the name is a plain identifier
        # (dev/test/prod/app), safe to inline. A search_path naming a
        # not-yet-created schema is harmless — _ensure_schema creates it next.
        connect_kwargs["options"] = f"-c search_path={schema}"
    conn = psycopg2.connect(**connect_kwargs)
    if schema:
        _ensure_schema(conn, schema)
    return conn


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


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped Session, closed after the route runs."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()
