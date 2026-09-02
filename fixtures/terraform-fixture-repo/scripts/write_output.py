#!/usr/bin/env python3
"""Write this apply's Terraform outputs directly to Lakebase (ADR-0002).

Run by the fixture repo's `apply` job (see
`.github/workflows/terraform.yml`) after `terraform apply`, on the merged
PR's own branch checkout. The provisioning API opens every Step's PR from a
branch named `provision/<request_id>/<step_key>` (see
`server.orchestrator._claim_and_open_next`) — that branch name is the only
context this Action has about which Step it's applying, so it's parsed
here rather than threaded through some new inbound channel (there is
deliberately no inbound endpoint on the API, per ADR-0002).

Connects to the same Lakebase instance the provisioning API uses, mirroring
`server.database._fetch_lakebase_credential`: either a full `DATABASE_URL`
(simplest for a manual/local run against a plain test Postgres), or a
Databricks-SDK-minted credential against `LAKEBASE_INSTANCE_NAME` (the real
deployed-Lakebase path, matching architecture.md §8/§10).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid

import psycopg2

_BRANCH_PATTERN = re.compile(r"^provision/(?P<request_id>[^/]+)/(?P<step_key>[^/]+)$")


def _terraform_outputs() -> dict:
    raw = subprocess.run(
        ["terraform", "output", "-json"], capture_output=True, text=True, check=True
    ).stdout
    return {key: value["value"] for key, value in json.loads(raw).items()}


def _connect():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)

    from databricks.sdk import WorkspaceClient

    instance_name = os.environ["LAKEBASE_INSTANCE_NAME"]
    w = WorkspaceClient()
    user = os.environ.get("PGUSER") or w.current_user.me().user_name
    credential = w.database.generate_database_credential(
        request_id=str(uuid.uuid4()), instance_names=[instance_name]
    )
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=user,
        password=credential.token,
        sslmode="require",
    )


def main() -> int:
    branch = os.environ["HEAD_REF"]
    match = _BRANCH_PATTERN.match(branch)
    if match is None:
        print(f"'{branch}' isn't a provisioning-API branch (provision/<request_id>/<step_key>) — skipping.")
        return 0

    request_id, step_key = match["request_id"], match["step_key"]
    outputs = _terraform_outputs()
    if not outputs:
        print("No Terraform outputs to write.")
        return 0

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT step.id FROM step
                JOIN provisioning_request ON provisioning_request.id = step.request_id
                WHERE provisioning_request.id = %s AND step.key = %s
                """,
                (request_id, step_key),
            )
            row = cur.fetchone()
            if row is None:
                print(f"No Step found for request {request_id!r} key {step_key!r} — skipping.", file=sys.stderr)
                return 1
            step_id = row[0]

            for key, value in outputs.items():
                cur.execute(
                    """
                    INSERT INTO output (id, step_id, key, value)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (step_id, key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (str(uuid.uuid4()), step_id, key, json.dumps(value)),
                )
                print(f"Wrote output {key}={value!r} for step {step_id}.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
