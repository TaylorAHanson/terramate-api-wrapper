# 0003 — Capture Step outputs via a GitHub Action → API PUT

- Status: Accepted
- Date: 2026-09-03
- Supersedes: [0002](0002-output-capture-via-direct-lakebase-write.md)

## Context

ADR-0002 decided that a Step's GitHub Action would write its Terraform outputs **directly to
Lakebase via the Databricks SDK**, and the API would keep polling GitHub only for status. That
choice bought "no inbound endpoint on the API," but at two costs it flagged as future
coordination:

- the Action needs a **standing Lakebase write credential** in the terramate repo's CI (a broad,
  long-lived DB credential living in a GitHub secret scope), and
- CI becomes **coupled to the `output` table's schema and connection** — a DB migration in this
  repo can now break a workflow in another repo, and the store has more than one writer.

Having built the surrounding machinery (the `applying` hold, #20; stuck-Step surfacing, #43; and
API auth, #47), those two costs read worse than the inbound-endpoint cost ADR-0002 was avoiding.
The apply run also produces the `terraform` **console text** we want to persist for status /
agent-reasoning (architecture.md "Persist Console Output"), which the direct-write path never
carried.

## Decision

The Step's **GitHub Action reports its apply result to the API over HTTP** instead of writing
Lakebase itself:

```
PUT /v1/requests/{id}/steps/{n}/outputs
{
  "applied": true | false,
  "outputs": { "workspace_id": 123456, ... },
  "tf_console": "<plan/apply console text>"
}
```

The route is **step-scoped** — the reporting Step is identified by the path (`{n}` = ordinal),
consistent with the existing `.../steps/{n}/plan` and `.../steps/{n}` routes — so outputs land
keyed by Step, matching how `consumes` resolves them.

The API validates the payload, persists the outputs into the `output` table (the same store,
templated **by reference** into the next Step's bundle exactly as before), records apply
success/failure and the console text, and advances the Step off `applying`. **The API is the
sole writer of Lakebase.** CI holds only a **scoped API credential** — no Lakebase credential and
no knowledge of the DB schema.

The **hold semantics are unchanged**: a Step stays at `applying` until its expected outputs are
present — now delivered by the PUT rather than a CI-side write — and a missing/partial/late
report still holds the Step for a human rather than opening the next PR with a blank reference.
The stuck-Step timeout (#43) still applies; it now measures "the report never arrived."

**Open (folds into the implementation tickets):** idempotency (an Action may retry the report)
and the exact M2M auth model for the CI caller are settled in the tickets.

## Consequences

- **Removes the cross-repo DB credential and schema coupling from CI** — the main win. CI no
  longer needs a Lakebase write credential or the `output` table's shape; it needs a scoped API
  token. The API stays the single writer of its own store.
- **New inbound endpoint to build and secure.** This is the cost ADR-0002 was avoiding; we now
  accept it. The endpoint is a privileged ingress (it writes provisioning truth), so it must
  authenticate the CI caller (M2M) — building on the auth hardening (#47) — and be **idempotent**
  under Action retries.
- **Captures `tf_console`** in the same call, which the direct-write path did not.
- **Reverses ADR-0002.** The "no inbound endpoint *on the API*" property is dropped deliberately;
  the "API doesn't read Terraform state / parse run logs" property still holds (the Action still
  computes outputs; it just reports them over HTTP instead of writing the DB).
- **Migration of existing engine work:** the orchestrator's "outputs present?" gate (#20) is now
  satisfied by the endpoint's write instead of a CI SDK write; the Seam-1 tests that *simulate* a
  direct Lakebase write become a call to the endpoint's persistence path. #43's stuck logic is
  unchanged in intent.
- The `output` row shape and the SCD2-history question (#6) are unchanged by this ADR — only the
  **ingress** changes.
