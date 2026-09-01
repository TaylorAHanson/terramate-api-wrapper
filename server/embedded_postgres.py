"""Bundled-binary embedded Postgres for local development.

`./dev.sh` needs to come up on a machine with no Docker and no
system-installed Postgres. `pgserver` ships real PostgreSQL binaries inside
its wheel (macOS arm64/x86_64, Linux x86_64, Windows), so booting one here
has no dependency beyond `pip install`. This is purely a local-dev
convenience — a deployed app never imports this module; it authenticates to
Lakebase instead (see server/database.py).

pgserver reaches its server over a unix-domain socket, so the connection URI
it returns puts the socket directory in the `host` *query parameter* rather
than the URL authority (`postgresql://postgres:@/<db>?host=<socket_dir>`).
`server/database.py::_connect` already falls back to that query param for
exactly this reason.
"""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_DATABASE_NAME = "terramate_dev"


def ensure_embedded_postgres(
    pgdata_dir: Path,
    database: str = DEFAULT_DATABASE_NAME,
    cleanup_mode: str | None = None,
) -> str:
    """Start (or reattach to) the embedded Postgres in `pgdata_dir`, creating
    `database` if needed, and return a DATABASE_URL for it.

    Idempotent and safe to call on every `./dev.sh` run: `pgserver.get_server`
    reattaches to an already-running server for the same data dir rather than
    re-initializing it. `cleanup_mode=None` (dev.sh's default) leaves the
    server (a detached `pg_ctl` daemon, not a child of this process) running
    once this process exits, so both the data and the running server survive
    a `./dev.sh` restart. Tests pass `cleanup_mode="delete"` instead, to stop
    the server and remove `pgdata_dir` once done.
    """
    import pgserver
    import psycopg2
    from psycopg2 import sql
    from sqlalchemy.engine import make_url

    pgdata_dir.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(pgdata_dir, cleanup_mode=cleanup_mode)

    admin_url = make_url(server.get_uri())
    # initdb is run with --auth=trust (local dev only, on a data dir no one
    # else can reach), so connecting as `postgres` needs no password.
    conn = psycopg2.connect(
        host=admin_url.host or admin_url.query.get("host"),
        port=admin_url.port,
        dbname=admin_url.database,
        user=admin_url.username,
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (database,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(database)))
    finally:
        conn.close()

    return server.get_uri(database=database)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgdata", required=True, type=Path, help="Persistent data directory")
    parser.add_argument("--database", default=DEFAULT_DATABASE_NAME)
    args = parser.parse_args()

    # Only the URL goes to stdout — callers (dev.sh) capture it directly.
    print(ensure_embedded_postgres(args.pgdata, args.database))


if __name__ == "__main__":
    _main()
