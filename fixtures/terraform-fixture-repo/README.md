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
  (on PR merge, then **reports the result to the deployed provisioning API over
  HTTP** — ADR-0003).
- `scripts/report_outputs.py` — the Action → API report (**ADR-0003**, current).
  Parses the merged PR's branch name (`provision/<request_id>/<step_key>`,
  minted by `server.orchestrator._claim_and_open_next`), resolves `step_key →
  ordinal` via the API, and `PUT`s the apply result to
  `/v1/requests/{id}/steps/{ordinal}/outputs`. Stdlib-only. See the full
  spec in [`docs/ci-integration-contract.md`](../../docs/ci-integration-contract.md).
- `scripts/write_output.py` — the **superseded ADR-0002** direct-Lakebase write,
  kept for reference only (no longer wired into the workflow).

## Required repo secrets (for `apply`'s ADR-0003 report)

The `apply` job reports via HTTP as a Databricks service principal (M2M — no
interactive browser in Actions):

- `APP_URL` — the deployed provisioning App's base URL
  (e.g. `https://terramate-api-wrapper-dev-….databricksapps.com`).
- `CI_DATABRICKS_HOST` — the workspace host the SP mints a token against.
- `CI_DATABRICKS_CLIENT_ID` / `CI_DATABRICKS_CLIENT_SECRET` — the CI service
  principal's OAuth client credentials. The SP must have **can use** on the App,
  and its forwarded identity must be on the App's `CI_PRINCIPALS` allowlist.

Set with `gh secret set APP_URL --repo <owner>/terramate-fixture-repo` (etc.).
Without these, `plan` still works (proves the PR/check-run half of the loop);
`apply` runs `terraform apply` but the report step fails — exactly the "Action
fails to report outputs" failure mode `architecture.md` §14 designs for (the
dependent Step just never gets claimed). See
[`docs/ci-integration-contract.md`](../../docs/ci-integration-contract.md) for
the full contract.

## Re-syncing after an edit here

Run `fixtures/push_fixture_repo.sh` from the `terramate-api-wrapper` repo
root — it force-pushes this directory's current content as `main` on the
standalone fixture repo (`gh` CLI auth required).
