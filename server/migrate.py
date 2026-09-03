"""Optional migrate-on-startup, env-gated (`RUN_MIGRATIONS_ON_STARTUP`).

Off by default. For a provisioning-truth DB, applying migrations is normally a
deliberate, separately-privileged, out-of-band step (see `migrations/env.py`):
auto-running `alembic upgrade head` on every boot means an unreviewed — possibly
destructive — migration lands automatically, and racing replicas contend on
DDL. When the flag is on (dev/test, or a target that accepts those trade-offs)
the app applies `alembic upgrade head` during startup (server.main lifespan)
instead of relying on that out-of-band step.

Concurrency: overlapping instances — a redeploy briefly runs old+new, and the
reconcile loop already assumes multi-replica (server.scheduler) — are serialized
by a Postgres session-level advisory lock held on a dedicated connection. Only
the instance that wins the lock runs the upgrade; the rest block, then find head
already current and no-op. If a holder dies mid-upgrade, Postgres releases the
lock when its connection drops.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from server.database import get_engine

logger = logging.getLogger(__name__)

# An arbitrary fixed 64-bit key so every instance contends on the *same*
# advisory lock; the value itself is irrelevant as long as it's constant across
# instances and unlikely to collide with another subsystem's advisory lock.
_MIGRATION_ADVISORY_LOCK_KEY = 0x7E44A7E5

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    """An Alembic `Config` pinned to this repo's `alembic.ini`/`migrations` by
    absolute path, so it resolves regardless of the process's working dir."""
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return config


def run_migrations_upgrade_head() -> None:
    """Apply `alembic upgrade head`, serialized across instances by a Postgres
    advisory lock. Blocking (DB + Alembic) — call it off the event loop.

    The lock is taken on its own AUTOCOMMIT connection so it is held for that
    connection's lifetime (a session-level lock, independent of any
    transaction) and never shares the connection Alembic runs the upgrade on.
    """
    engine = get_engine()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_ADVISORY_LOCK_KEY})
        logger.info("migrate_on_startup lock_acquired — applying alembic upgrade head")
        try:
            command.upgrade(_alembic_config(), "head")
            logger.info("migrate_on_startup upgrade_complete")
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_ADVISORY_LOCK_KEY})
