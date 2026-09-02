# terramate-fixture-repo

A throwaway GitHub repo standing in for the real Terramate/Terraform repo
(architecture.md §3.1's Phase 1 fixture), used by
[terramate-api-wrapper#22](https://github.com/TaylorAHanson/terramate-api-wrapper/issues/22)'s
Seam 3 suite to prove the real `GitHubClient`'s PR → merge → apply →
output-write → poll loop end to end, on safe resources (`random_id` /
`local_file` only — no cloud credentials, nothing billable).

This directory (`fixtures/terraform-fixture-repo/` in `terramate-api-wrapper`)
is the source of truth; it's pushed as the initial commit of the standalone
`terramate-fixture-repo` GitHub repo, which is what `RealGitHubClient` and
Seam 3 actually talk to over the GitHub API.

## Layout

- `main.tf` — trivial Terraform emitting a `workspace_id` output.
- `stacks/catalogs/research/catalog.tm.yaml`, `stacks/metastores/main/bindings.tm.yaml`
  — seeded catalyst-bundle files the `schema`/`workspace` Recipes' `EditFile`s
  target (`server.recipes.schema.locate_catalog`,
  `server.recipes.workspace.locate_metastore_binding`).
- `.github/workflows/terraform.yml` — `plan` (on PR open/update, publishes a
  `terraform-plan` check run `RealGitHubClient.get_plan` reads) and `apply`
  (on PR merge, then writes Terraform's outputs to Lakebase — ADR-0002).
- `scripts/write_output.py` — the Action → Lakebase write. Parses the merged
  PR's branch name (`provision/<request_id>/<step_key>`, minted by
  `server.orchestrator._claim_and_open_next`) to know which `step` row to
  write the `output` row against.

## Required repo secrets (for `apply`'s Lakebase write)

Either:

- `SEAM3_DATABASE_URL` — a full Postgres connection string (simplest; points
  at a plain test Postgres reachable from GitHub-hosted runners), **or**
- `SEAM3_DATABRICKS_HOST` / `SEAM3_DATABRICKS_CLIENT_ID` /
  `SEAM3_DATABRICKS_CLIENT_SECRET` (a service-principal M2M credential —
  there's no interactive browser in Actions) / `SEAM3_LAKEBASE_INSTANCE_NAME`
  / `SEAM3_PGHOST` / `SEAM3_PGPORT` / `SEAM3_PGDATABASE` / `SEAM3_PGUSER` —
  the real deployed-Lakebase path, mirroring `server.database`.

Set with `gh secret set SEAM3_DATABASE_URL --repo <owner>/terramate-fixture-repo`.
Without these, `plan` still works (proves the PR/check-run half of the loop);
`apply` runs `terraform apply` but its Lakebase write step fails, which is
exactly the "Action fails to write outputs" failure mode architecture.md §14
already designs for (the dependent Step just never gets claimed).

## Re-syncing after an edit here

Run `fixtures/push_fixture_repo.sh` from the `terramate-api-wrapper` repo
root — it force-pushes this directory's current content as `main` on the
standalone fixture repo (`gh` CLI auth required).
