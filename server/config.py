"""Environment configuration.

Every value here is read from an env var, never from a file on disk — in a
deployed Databricks App these are injected by the platform (either directly,
or `valueFrom` a secret-scope-backed app resource; see databricks.yml and
architecture.md §10). No caching: reading `os.environ` is cheap, and caching
would make env-var overrides in tests (monkeypatch) silently stale.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_environment: str
    github_pat: str | None
    # "owner/name" of the Terramate repo `RealGitHubClient` opens PRs against
    # (see server.github_client, #22). Unset locally/in Seam 1-2 tests, which
    # never construct a real client.
    github_repo: str | None

    # Local/dev/test override: a full SQLAlchemy connection string to a plain
    # Postgres instance. When set, `server.database` skips the Lakebase OAuth
    # credential flow entirely. This is how tests point at a real test
    # Lakebase-compatible Postgres (Seam 1) instead of the managed service.
    database_url: str | None

    # The production (Databricks App) path: PGHOST/PGPORT/PGDATABASE/PGUSER are
    # injected by the app's `postgres` resource attachment; LAKEBASE_INSTANCE_NAME
    # is set explicitly in databricks.yml so the app knows which instance to mint
    # an OAuth credential for.
    pg_host: str | None
    pg_port: str | None
    pg_database: str | None
    pg_user: str | None
    lakebase_instance_name: str | None

    # How often the in-process reconcile driver (server.scheduler, #39) calls
    # orchestrator.tick(). The driver only starts when a real GitHubClient can
    # be built (GITHUB_PAT + GITHUB_REPO); locally/in tests it stays off, so
    # this value is unused there.
    reconcile_interval_seconds: float

    # Root log level for the app's `server.*` loggers (server.logging_config,
    # #41). `INFO` surfaces state transitions and PR opens; `DEBUG` additionally
    # surfaces the per-call GitHub transport lines.
    log_level: str

    # How long a Step may sit at `applying` (waiting on its Action's ADR-0002
    # output write) before the reconcile loop flags it `stuck` and logs a
    # warning (server.orchestrator, #43). Conservative by default so a merely-
    # slow apply doesn't false-positive.
    step_stuck_threshold_seconds: float

    # The forwarded identities (Databricks Apps `X-Forwarded-Email` /
    # `X-Forwarded-User`, see server.auth, #47) allowed to read/flip the
    # global intake off-switch. Comma-separated; empty means nobody is
    # authorized, which is the safe default until an operator sets it.
    admin_principals: frozenset[str]

    # The forwarded identities authorized to call the ADR-0003 output-report
    # ingress (`PUT .../steps/{n}/outputs`, #55) — the CI M2M service
    # principal a Step's GitHub Action authenticates as. Same trust model as
    # `admin_principals`: read from the platform-stamped forwarded headers
    # (server.auth), never from a client-controlled one. Comma-separated;
    # empty means no caller is authorized, the safe default until an
    # operator registers the CI principal per target.
    ci_principals: frozenset[str]


def get_settings() -> Settings:
    return Settings(
        app_environment=os.environ.get("APP_ENVIRONMENT", "local"),
        github_pat=os.environ.get("GITHUB_PAT") or None,
        github_repo=os.environ.get("GITHUB_REPO") or None,
        database_url=os.environ.get("DATABASE_URL") or None,
        pg_host=os.environ.get("PGHOST"),
        pg_port=os.environ.get("PGPORT"),
        pg_database=os.environ.get("PGDATABASE"),
        pg_user=os.environ.get("PGUSER"),
        lakebase_instance_name=os.environ.get("LAKEBASE_INSTANCE_NAME"),
        reconcile_interval_seconds=float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "15")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        step_stuck_threshold_seconds=float(os.environ.get("STEP_STUCK_THRESHOLD_SECONDS", "3600")),
        admin_principals=frozenset(
            p.strip() for p in os.environ.get("ADMIN_PRINCIPALS", "").split(",") if p.strip()
        ),
        ci_principals=frozenset(
            p.strip() for p in os.environ.get("CI_PRINCIPALS", "").split(",") if p.strip()
        ),
    )
