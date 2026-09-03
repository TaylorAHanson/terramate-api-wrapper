# 0002 — Capture Step outputs via a direct GitHub Action → Lakebase write

- Status: Superseded by [0003](0003-output-capture-via-action-api-put.md)
- Date: 2026-09-01

## Context

A Step's `apply` produces Terraform outputs (e.g. a `workspace_id`) that a later Step in the
same Playbook must consume — this is the whole reason ordering and a queue exist (see
`CONTEXT.md`, §5.1 of the architecture proposal). The open question was **how the outputs of an
applied Step get from the GitHub Actions run into Lakebase**, where the API can template them
into the next Step's bundle.

Options considered at the architecture-defense meeting:

1. **API polls the Actions run** and parses outputs from a structured check-run output.
2. **API polls the PR** and parses outputs from a comment / body.
3. **GitHub Action POSTs** outputs to an inbound API endpoint.
4. **GitHub Action writes outputs directly to Lakebase** via the Databricks SDK.

Options 1–2 mean parsing run logs / check-runs / comments — brittle, format-coupled, and
dependent on Terramate's *undocumented* native output-sharing. Option 3 requires standing up
and securing an inbound endpoint on the API. The API already treats GitHub as an
eventually-consistent source it polls for *status*, and Lakebase is the single durable store.

## Decision

The Step's **GitHub Action writes its Terraform outputs directly to Lakebase via the Databricks
SDK**, into the `output` table, keyed by Step. The API continues to poll GitHub only for
*status* (PR / checks / merge / apply-complete); it does **not** read Terraform state, parse run
logs, or expose an inbound endpoint. Once the Action reports apply-complete, the API reads the
captured outputs from Lakebase and templates them **by reference** into the next Step's bundle.

Whether the `output` table keeps history as **SCD2** is left open and tracked on the map (#6).

## Consequences

- **Simplest reliable path:** no log/check-run/comment parsing, no dependence on Terramate's
  undocumented output-sharing, and no inbound API endpoint to build or secure.
- **New cross-repo dependency:** the terramate repo's CI must be changed to perform the write,
  and the Action needs a **Lakebase write credential** (from a secret scope). This is
  coordination time with the platform team, tracked on the map (#6).
- **New failure mode:** an Action that fails to write (or writes partial) outputs. Mitigated by
  not marking a Step `applied` until its expected outputs are present in Lakebase; a
  missing/partial write holds the Step for a human rather than opening the next PR with a blank
  reference.
- **Reverses earlier proposal text.** The initial proposal had the API *pull* outputs from the
  Actions run and made a virtue of "no inbound access." Outputs now arrive by *push* to
  Lakebase; the "no inbound endpoint *on the API*" property still holds, since the Action writes
  to the store, not to the API.
- The exact row shape and SCD2 decision fold into the Recipe/output work (#5, #6).
