"""Alembic environment: connects via `server.database.get_engine()` rather
than a static `sqlalchemy.url`, since a production connection needs a
freshly-minted Lakebase OAuth credential (see server/database.py) that a
config file can't hold.
"""
from __future__ import annotations

from alembic import context

from server.database import get_engine

target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError(
        "Offline migrations are not supported — Lakebase connections require a "
        "live OAuth credential fetch. Run `alembic upgrade head` with a reachable "
        "database (DATABASE_URL, or a deployed app's Lakebase resource)."
    )


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
