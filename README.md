# terramate-api-wrapper

A versioned provisioning abstraction API between a self-service (agentic) app
and the Terramate/Terraform codebase that provisions Databricks resources. See
[`architecture.md`](https://github.com/TaylorAHanson/terramate-api-wrapper/blob/wayfinder/architecture-proposal/architecture.md)
and [`CONTEXT.md`](https://github.com/TaylorAHanson/terramate-api-wrapper/blob/wayfinder/architecture-proposal/CONTEXT.md)
for the design.

Databricks App: FastAPI backend (`server/`) + React frontend (`frontend/`),
durable state in Lakebase (managed Postgres), migrated with Alembic
(`migrations/`).

See [`AGENTS.md`](./AGENTS.md) for the full operating guide — orientation,
the dev loop and Seam test model, adding a new resource Type, changing the
schema, and the contribution flow.

## Local development

```
./dev.sh
```

That's it — no Docker and no system-installed Postgres required. `dev.sh`
creates a virtualenv, installs Python deps, boots a bundled embedded Postgres
(real PostgreSQL binaries, shipped via the `pgserver` package, persisted in
the gitignored `.pgdata/` dir so it survives restarts), applies
`alembic upgrade head`, then starts the FastAPI backend (`:8000`) and the
Vite frontend (`:5173`, installing `frontend/node_modules` on first run).

Pass `--debug` to start the backend under `debugpy` on port 5678.

Bring your own Postgres instead by setting `DATABASE_URL` in `.env` (copied
from `.env.example` on first run) — `dev.sh` uses it as-is and skips the
embedded Postgres:

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_dev ./dev.sh
```

The deployed Databricks App path is unaffected either way: with
`DATABASE_URL` unset and `LAKEBASE_INSTANCE_NAME` set, the app authenticates
to Lakebase via OAuth instead (see `server/database.py`).

## Tests

```
pytest
```

No Docker, service container, or system Postgres needed — the test session
boots its own ephemeral embedded Postgres (real PostgreSQL, bundled binaries)
against a temp data dir, applies the Alembic migrations to it, and tears it
down at the end (see `tests/conftest.py`). Seam 1 still runs against a real
test Lakebase, not a mock, and the reconcile-loop tests still exercise real
`FOR UPDATE SKIP LOCKED` row-locking semantics.

To run against your own Postgres instead, set `DATABASE_URL` explicitly — it
always takes precedence:

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_test pytest
```

This runs Seam 1 (`tests/seam1_api/`), Seam 2 (`tests/seam2_recipes/`), and the
fast unit tests (`tests/unit/`) — never Seam 3 (see below), which is excluded
by default (`pyproject.toml`'s `addopts`).

### Seam 3: the real GitHub fixture-repo loop

`tests/seam3_fixture_e2e/` proves the real `GitHubClient`
(`server.github_client.RealGitHubClient`) end to end — PR → merge → apply →
Lakebase output-write → poll — against a real, throwaway GitHub repo
(`fixtures/terraform-fixture-repo/`, pushed as
[`TaylorAHanson/terramate-fixture-repo`](https://github.com/TaylorAHanson/terramate-fixture-repo)
via `fixtures/push_fixture_repo.sh`). It drives real GitHub Actions runs, so
it's gated behind an explicit opt-in and its own env vars — see
`tests/seam3_fixture_e2e/conftest.py`:

```
GITHUB_PAT=... SEAM3_DATABASE_URL=<postgres reachable from GitHub-hosted runners> \
  pytest -m seam3e2e
```

`SEAM3_DATABASE_URL` must already have migrations applied (`alembic upgrade
head` against it) and must be the **same** connection string configured as
the fixture repo's own `SEAM3_DATABASE_URL` (or `SEAM3_DATABRICKS_*`/`SEAM3_PG*`)
secret — see `fixtures/terraform-fixture-repo/README.md` — since the
Action's write and this test's poll both need to see the same `output` row.
Without that secret configured, `apply` still runs (proving the merge/apply
half) but the Lakebase-write step fails cleanly — the "Action fails to write
outputs" failure mode architecture.md §14 already designs for.

## Deploying

Deployed as a Databricks App via `databricks.yml` (Databricks Asset Bundle).
The GitHub service-account PAT must exist in the secret scope named by
`var.secret_scope` before the first deploy (see the comment in
`databricks.yml`):

```
databricks bundle deploy -t dev
```
