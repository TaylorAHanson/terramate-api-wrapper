"""Integration test for the embedded-Postgres bootstrap helper — a real
`pgserver` instance, not a mock (matches this repo's Seam 1 philosophy of
testing against a real Postgres rather than faking it). Uses
`cleanup_mode="delete"` so the server is stopped and its data dir removed at
the end of the test, unlike `./dev.sh`'s own use of this helper.
"""
from __future__ import annotations

from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from server.embedded_postgres import ensure_embedded_postgres


@pytest.fixture()
def pgdata_dir(tmp_path: Path) -> Path:
    return tmp_path / "pgdata"


def _connect(database_url: str) -> psycopg2.extensions.connection:
    url = make_url(database_url)
    return psycopg2.connect(
        host=url.host or url.query.get("host"),
        port=url.port,
        dbname=url.database,
        user=url.username,
    )


def test_ensure_embedded_postgres_boots_a_real_working_postgres(pgdata_dir: Path) -> None:
    database_url = ensure_embedded_postgres(
        pgdata_dir, database="embedded_test_db", cleanup_mode="delete"
    )

    conn = _connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            assert cur.fetchone() == (1,)
    finally:
        conn.close()


def test_ensure_embedded_postgres_reattaches_instead_of_reinitializing(pgdata_dir: Path) -> None:
    first_url = ensure_embedded_postgres(
        pgdata_dir, database="embedded_test_db", cleanup_mode="delete"
    )
    conn = _connect(first_url)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE persisted (id int);")
        conn.commit()
    finally:
        conn.close()

    # Same pgdata_dir, called again — should reattach and see the same data,
    # not wipe/reinitialize it.
    second_url = ensure_embedded_postgres(
        pgdata_dir, database="embedded_test_db", cleanup_mode="delete"
    )
    assert second_url == first_url

    conn = _connect(second_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM persisted;")
    finally:
        conn.close()
