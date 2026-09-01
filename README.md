# terramate-api-wrapper

A versioned provisioning abstraction API between a self-service (agentic) app
and the Terramate/Terraform codebase that provisions Databricks resources. See
[`architecture.md`](https://github.com/TaylorAHanson/terramate-api-wrapper/blob/wayfinder/architecture-proposal/architecture.md)
and [`CONTEXT.md`](https://github.com/TaylorAHanson/terramate-api-wrapper/blob/wayfinder/architecture-proposal/CONTEXT.md)
for the design.

Databricks App: FastAPI backend (`server/`) + React frontend (`frontend/`),
durable state in Lakebase (managed Postgres), migrated with Alembic
(`migrations/`).

## Local development

Backend:

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker run -d --name terramate-pg -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=terramate_dev -p 5432:5432 postgres:16-alpine
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_dev alembic upgrade head
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_dev uvicorn server.main:app --reload
```

Frontend:

```
cd frontend
npm install
npm run dev
```

## Tests

Point `DATABASE_URL` at a real Postgres — Seam 1 runs against a real test
Lakebase, not a mock (see `tests/conftest.py`):

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_test pytest
```

## Deploying

Deployed as a Databricks App via `databricks.yml` (Databricks Asset Bundle).
The GitHub service-account PAT must exist in the secret scope named by
`var.secret_scope` before the first deploy (see the comment in
`databricks.yml`):

```
databricks bundle deploy -t dev
```
