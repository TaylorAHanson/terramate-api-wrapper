"""Unit tests for server.database's DATABASE_URL connection logic.

No real Postgres needed here: `_connect()`'s `psycopg2.connect` call is
monkeypatched, unlike the Seam 1 fixtures in conftest.py which deliberately
run against a real test Lakebase. This covers the one behavior that's easy
to get wrong silently: the embedded local-dev Postgres (server/
embedded_postgres.py) is reached over a unix-domain socket, so its URL has
no authority host — see the matching comment in server/database.py.
"""
from __future__ import annotations

import pytest

from server import database


@pytest.fixture()
def captured_connect(monkeypatch):
    calls: dict = {}

    def fake_connect(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.setattr("psycopg2.connect", fake_connect)
    return calls


def test_connect_uses_authority_host_for_a_normal_url(monkeypatch, captured_connect):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://user:pw@localhost:5432/mydb")

    database._connect()

    assert captured_connect["host"] == "localhost"
    assert captured_connect["port"] == 5432
    assert captured_connect["dbname"] == "mydb"
    assert captured_connect["user"] == "user"


def test_connect_falls_back_to_query_host_for_a_unix_socket_url(monkeypatch, captured_connect):
    # pgserver's embedded-Postgres URL shape: the socket directory lives in
    # the `host` query param, since a unix-domain socket has no host/port.
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:@/terramate_dev?host=/tmp/some/socket/dir",
    )

    database._connect()

    assert captured_connect["host"] == "/tmp/some/socket/dir"
    assert captured_connect["port"] is None
    assert captured_connect["dbname"] == "terramate_dev"
    assert captured_connect["user"] == "postgres"
