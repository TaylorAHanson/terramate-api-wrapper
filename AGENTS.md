# AGENTS.md

Operating guide for any coding agent (human or AI) working in this repo. It
assumes no prior knowledge of this project's tooling or conventions. For
domain vocabulary (Type, Recipe, Playbook, Step, Output, …) and design
rationale, `CONTEXT.md` and `docs/adr/` are authoritative — this file orients
you and points there rather than repeating them.

## 1. Orientation / mental model

This is a versioned provisioning API sitting between a self-service
(agentic) client and the Terramate/Terraform codebase that actually
provisions Databricks resources (a separate repo, referred to here as "the
terramate repo"). A client `POST`s a small, stable request; this API knows
the "tribal knowledge" of turning that into the right file edits and
pull-request ordering.

**The single most important boundary: this API never runs Terraform.** It is
a **PR-orchestration + polling + output-capture engine**: a client `POST`s a
`ProvisioningRequest`, the API expands it into a `Playbook` of `Step`s and,
for each Step, opens a **pull request** against the terramate repo's bundle
files — it never invokes `terraform plan`/`apply` itself. That repo's own
**GitHub Actions** run plan/apply for the PR; a human reviews and merges it
(the approval gate); the API polls GitHub for status and, once applied,
reads that Step's **Output**s back from Lakebase rather than parsing
Terraform state or run logs (the GitHub Action writes them there directly —
see `docs/adr/0002-output-capture-via-direct-lakebase-write.md`). Only once a
Step's dependencies and Outputs are satisfied does the API move on to the
next Step. Terraform execution and state are entirely out of scope and live
in the terramate repo's CI — see `CONTEXT.md` for what each capitalized term
above means precisely.

**Layer map:**

- `server/` — FastAPI backend. Routes in `server/routes/`, the Recipe
  framework and per-type Recipes in `server/recipes/`, the reconcile loop in
  `server/orchestrator.py`, ORM models in `server/models.py`.
- `frontend/` — React + TypeScript (Vite).
- Durable state lives entirely in **Lakebase** (managed Postgres), migrated
  with **Alembic** (`migrations/`). There is **no durable local filesystem**
  — bundle edits happen via the GitHub API against an ephemeral checkout and
  are never persisted to local disk.
- Deployed together as one **Databricks App** (`databricks.yml`); locally
  they run as two separate dev processes (see §2).

For the full glossary (ProvisioningRequest, Type, Recipe, Playbook, Step,
Bundle, Output, Version, Preflight/Postflight check, Asset identifier,
Idempotency-Key) read `CONTEXT.md`. For *why* things are shaped this way,
read `docs/adr/` — currently:

- `docs/adr/0001-imperative-recipes-over-declarative-registry.md` — why each
  Type is an imperative Recipe in code, not a declarative schema.
- `docs/adr/0002-output-capture-via-direct-lakebase-write.md` — why Step
  Outputs are written to Lakebase directly by the GitHub Action, not polled
  or pushed to an API endpoint.

`architecture.md` at the repo root has the original, fuller design proposal
if you need more depth than the ADRs and glossary provide; where it and
`docs/adr/` disagree, the ADRs win (they're the later, accepted decisions).

## 2. Operate (dev loop)

### Setup and run

```
./dev.sh
```

That's it — **no Docker and no system-installed Postgres required.**
`dev.sh`:

1. Creates a Python virtualenv and installs `requirements-dev.txt`.
2. If `DATABASE_URL` is not set (via `.env` or the environment), boots a
   **bundled embedded Postgres** — real PostgreSQL binaries shipped via the
   `pgserver` Python package (`server/embedded_postgres.py`), persisted in
   the gitignored `.pgdata/` directory so data survives restarts across runs.
3. Runs `alembic upgrade head` against whichever Postgres is active.
4. Starts the FastAPI backend on `:8000` and the Vite frontend dev server on
   `:5173` (installing `frontend/node_modules` on first run). The Vite dev
   server proxies `/v1` and `/version` to the backend — see
   `frontend/vite.config.ts`.

Pass `--debug` to start the backend under `debugpy` on port `5678` instead.

To bring your own Postgres instead of the embedded one, copy `.env.example`
to `.env` (done automatically on first run) and set `DATABASE_URL`:

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_dev ./dev.sh
```

When `DATABASE_URL` is unset and `LAKEBASE_INSTANCE_NAME` is set instead (the
deployed-app path), the app authenticates to Lakebase via OAuth through the
Databricks SDK — see `server/database.py` and `server/config.py`. Locally,
you will essentially always be on the embedded-Postgres or your-own-Postgres
path, not this one.

There is nothing to `docker compose up`, and no "install Postgres locally"
step — do not reintroduce either.

### Tests

```
pytest
```

No Docker, service container, or system Postgres needed here either: the
test session (`tests/conftest.py`) boots its own **ephemeral** embedded
Postgres against a fresh temp data dir, applies the real Alembic migrations
to it, runs the suite, and tears it down at the end. If `DATABASE_URL` is
already set in the environment, that takes precedence and nothing is
provisioned.

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/terramate_test pytest
```

**The Seam model** (how this repo scopes what a test proves):

- `tests/seam1_api/` — **Seam 1**: drive the real HTTP API against a real
  test Lakebase (real `SELECT ... FOR UPDATE SKIP LOCKED` row-locking
  semantics included). The only fake is `GitHubClient`
  (`tests/seam1_api/fakes.py`) — there is no live GitHub call in tests.
  Covers request lifecycle, the reconcile loop, and Step ordering.
- `tests/seam2_recipes/` — **Seam 2**: golden-file tests for Recipes. No
  HTTP, no database, no GitHub — pure, fast tests that assert a Recipe's
  `build(params) -> Playbook` produces an exact, checked-in shape
  (`tests/seam2_recipes/golden/*.json`/`.yaml`), via the harness in
  `tests/seam2_recipes/golden_harness.py`. Set `UPDATE_GOLDEN=1` when
  re-running to intentionally regenerate a golden file after a deliberate
  Recipe change.
- `tests/unit/` — fast, no-DB unit tests below the HTTP boundary (e.g.
  `RealGitHubClient`'s request-building, the orchestrator's bundle-edit
  substitution logic).
- `tests/seam3_fixture_e2e/` — **Seam 3**: proves the real `GitHubClient`
  (`server.github_client.RealGitHubClient`) end to end — PR → merge → apply
  → Lakebase output-write → poll — against a real, throwaway GitHub repo
  (`fixtures/terraform-fixture-repo/`). It drives real GitHub Actions runs,
  so a bare `pytest` **never** picks it up (`pyproject.toml`'s
  `addopts = "-m 'not seam3e2e'"`); it needs its own env vars and an explicit
  `pytest -m seam3e2e` — see `tests/seam3_fixture_e2e/conftest.py` and the
  "Seam 3" section of `README.md` for the exact invocation. You will not run
  this in ordinary dev-loop or CI usage.
- `tests/test_database.py`, `tests/test_embedded_postgres.py` — infrastructure
  tests for the embedded-Postgres bootstrap itself.

Run a single file or test the normal pytest way, e.g.
`pytest tests/seam2_recipes/test_workspace_recipe.py -v`. A bare `pytest`
runs Seam 1, Seam 2, and `tests/unit/`, but never Seam 3.

### Typecheck / build

Frontend: `cd frontend && npm run build` runs `tsc -b` (typecheck) then
`vite build`. There is no separate Python type-checker or linter configured
in this repo today — don't assume one exists.

### Project layout

```
server/
  routes/        FastAPI routers (health, requests, admin)
  recipes/       Recipe framework + per-type Recipes + the registry
  models.py      SQLAlchemy ORM models (mirrors migrations/versions/, DDL is authoritative)
  database.py    Engine/session setup: DATABASE_URL vs. Lakebase OAuth
  orchestrator.py  The reconcile loop (claims and advances queued Steps)
  intake_gate.py Global on/off switch for new requests
  github_client.py  The GitHub API client (faked in Seam 1 tests)
frontend/        React + TypeScript app (Vite)
migrations/      Alembic environment + versioned revisions
fixtures/        The throwaway terraform-fixture-repo Seam 3 drives, + the
                 script that pushes it to its own GitHub repo
tests/
  seam1_api/     Seam 1 (real HTTP + real test Lakebase, GitHub faked)
  seam2_recipes/ Seam 2 (pure Recipe golden-file tests)
  seam3_fixture_e2e/  Seam 3 (real GitHubClient against the real fixture repo, opt-in only)
  unit/          Fast no-DB unit tests below the HTTP boundary
docs/adr/        Accepted architectural decisions
CONTEXT.md       Domain glossary (source of truth for terminology)
architecture.md  Fuller original design proposal (ADRs supersede it on conflicts)
```

## 3. Add a new resource Type

This is the main extension point. To add Type `foo`:

1. **Write a Recipe.** Create `server/recipes/foo.py` modeled on
   `server/recipes/schema.py` (single-Step, no Outputs) or
   `server/recipes/workspace.py` (multi-Step, with `produces`/`consumes`
   wiring between Steps). Define:
   - A Pydantic `FooParams` model for the type's params.
   - A Pydantic `FooProvisioningRequest` model with
     `type: Literal["foo"]` and `params: FooParams` — this is the
     request-envelope member for this Type.
   - A `FooRecipe(Recipe)` class with `type = "foo"`, `params_model =
     FooParams` (the orchestrator uses this to rebuild the exact same
     Playbook from a persisted request's `params` dict at claim time — see
     `Recipe.build_from_params_dict` in `server/recipes/framework.py`), and a
     `build(self, params: FooParams) -> Playbook` method that returns a
     `Playbook` of one or more `StepSpec`s (see `server/recipes/framework.py`
     for `StepSpec`, `AddFile`, `EditFile`, `OutputRef`). Bundle edits are
     always parse → mutate → serialize (`EditFile.patch` takes and returns a
     parsed YAML dict) — never raw text munging — so the resulting PR diff
     stays reviewable.
2. **Register it.** Add an entry to `RECIPES` in
   `server/recipes/registry.py`:
   ```python
   "foo": FooRecipe(),
   ```
3. **Wire the request envelope.** In `server/routes/requests.py`, add
   `FooProvisioningRequest` to the `ProvisioningRequestBody` discriminated
   Union (the `Field(discriminator="type")` annotation) so `POST /v1/requests`
   accepts and validates `type: "foo"` bodies against `FooParams`.
4. **Add a Seam-2 golden-file test.** Create
   `tests/seam2_recipes/test_foo_recipe.py` modeled on
   `tests/seam2_recipes/test_workspace_recipe.py`: build a `Playbook` from
   representative params and call
   `assert_matches_golden(playbook, GOLDEN_DIR / "foo_case.json")`. On first
   run (or after an intentional change), run with `UPDATE_GOLDEN=1` to write
   the golden file, inspect the generated JSON by eye to confirm it's what
   you expect, then commit it and rerun without the env var to confirm it
   now passes as a regression check.

**Verify:** `pytest tests/seam2_recipes/test_foo_recipe.py -v` should pass
once the golden file is checked in. Then run the full suite
(`pytest`) to confirm the new registry entry and request-envelope wiring
didn't break Seam 1's request-lifecycle tests
(`tests/seam1_api/test_requests_api.py`).

## 4. Change the schema

Schema changes go through **Alembic** (`migrations/`); `server/models.py`
(the SQLAlchemy ORM) must always mirror the migrations exactly — the
migration is the authoritative DDL, the ORM classes are just the mapping the
rest of the app reads and writes through.

1. **Create a revision:**
   ```
   alembic revision -m "add foo column"
   ```
   This creates `migrations/versions/<rev>_add_foo_column.py`. Fill in
   `upgrade()` and `downgrade()` using `sqlalchemy as sa` / `alembic.op`, following
   the existing revisions in `migrations/versions/` as examples.
2. **Set `down_revision` correctly.** Check
   `alembic heads` (or just read the latest file in `migrations/versions/`)
   for the current head *on your base branch* before creating your revision
   — two branches created off the same head can both mint a revision id that
   collides once merged. If that happens, renumber the later one and repoint
   its `down_revision` (see the note at the top of
   `migrations/versions/0003_intake_gate.py` for a worked example from this
   repo's history).
3. **Update `server/models.py`** to match the new DDL exactly (column name,
   nullability, type, default).
4. **Apply it locally:** `alembic upgrade head` (or just restart `./dev.sh`,
   which runs this for you). `alembic downgrade -1` reverts the most recent
   revision if you need to back out.
5. **In CI / the deployed Lakebase:** the same `alembic upgrade head`
   command applies migrations before the app starts against a real
   Postgres-compatible database — there's no separate migration path for
   test vs. deployed. `migrations/env.py` connects via
   `server.database.get_engine()` rather than a static URL, since a
   deployed run needs a freshly-minted Lakebase OAuth credential; **offline
   migrations are intentionally unsupported** (`alembic upgrade head` needs
   a reachable database, always).

**Portability conventions** this repo already follows — match them in new
migrations:

- Primary/foreign-key ids are `sa.String(36)` (UUIDs stored as text), not a
  database-specific UUID type.
- JSON columns use the generic `sa.JSON`, not a Postgres-specific JSONB type.
- Timestamps default via `server_default=sa.func.now()` (a DB-side default,
  so the ORM omits these columns from `INSERT`s rather than sending an
  explicit value) and use `sa.DateTime(timezone=True)`.
- New, not-yet-used columns are added `nullable=True` as explicit "reserved
  seams" (see `provisioning_request.asset_id`, `step.lock_token`) rather than
  guessed-at NOT NULL columns for a feature that hasn't landed yet.

## 5. Ship a change

1. **Branch** off `main`.
2. **Commit** your changes with a message describing the *why*, not just the
   *what*.
3. Run `pytest` (backend) and, if you touched the frontend,
   `cd frontend && npm run build` (typecheck + build) before opening a PR.
4. **Open a pull request** against `main`.
5. **CI**: `.github/workflows/ci.yml` defines a `backend` job (installs
   `requirements-dev.txt`, runs `alembic upgrade head` against a Postgres
   service container, then `pytest`) and a `frontend` job (`npm ci` then
   `npm run build`) on every push to `main` and every PR. As of this
   writing both jobs are gated off (`if: false`) pending re-enablement —
   check the current state of that file rather than assuming either status.
6. **Write an ADR** under `docs/adr/` (numbered, following the format of
   `docs/adr/0001-imperative-recipes-over-declarative-registry.md`) when your
   change makes or reverses an architecturally significant decision — e.g.
   introducing a new mechanism, changing how a layer talks to another layer,
   or overturning a decision an existing ADR recorded. A routine bug fix, a
   new Type's Recipe, or a straightforward schema addition does not need
   one; a new cross-cutting mechanism or a reversal of existing behavior
   does. If in doubt, a short ADR is cheap — write one.
