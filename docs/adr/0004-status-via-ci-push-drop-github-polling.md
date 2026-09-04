# 0004 — Status via CI push; drop GitHub polling

- Status: Accepted
- Date: 2026-09-04
- Supersedes the status-polling half of the design assumed by [0002](0002-output-capture-via-direct-lakebase-write.md) / [0003](0003-output-capture-via-action-api-put.md), architecture.md §4.2/§5/§6, and the plan-surfacing/PR-lifecycle work in #19/#45.

## Context

ADR-0003 made a Step's **apply result** a push: the CI `PUT`s `{applied, outputs, tf_console}`
to the API. But the API still **polls GitHub** for the rest of a Step's lifecycle — the reconcile
loop calls `get_plan` (reads the `terraform-plan` check run) to leave `pr_open`, and
`get_pull_request_status` (reads merge/close) to leave `awaiting_approval` — and models the GitHub
PR lifecycle as Step states (`pr_open`, `awaiting_approval`, `applying`).

Once the apply result is a push, that polling is redundant infrastructure to build, test, and run:

- **`terraform apply` only runs after a merge**, so a push saying "applied" *inherently* means
  "merged and applied." Polling to separately detect the merge tells us nothing the push doesn't.
- The **`applying` hold** (#20) only existed to bridge the ADR-0002 gap between "merge detected
  (poll)" and "outputs arrived later (async DB write)." One atomic result push closes that gap.
- The **plan/approval gate is naturally a GitHub-side human activity**: a reviewer reads the PR and
  its `terraform-plan` check and merges to approve. The self-service app does not need the plan in
  Phase 1, so the API has no reason to poll for it or hold a state waiting on it.

What the API cares about is the **real-world outcome** of each PR, and that is exactly what a push
carries. Merge, plan-ready, and the intermediate lifecycle are GitHub's concern, not the API's.

## Decision

**The API opens PRs and never polls GitHub for status. Every Step transition after "PR opened" is
driven by a CI push.** The Phase-1 Step lifecycle collapses to:

```
queued → submitted → { done | failed | rejected }
```

- **queued** — dependencies not yet `done`, or intake gated; no PR yet.
- **submitted** — the API has opened the Step's PR and is waiting for CI's terminal push. (Replaces
  `pr_open` + `awaiting_approval` + `applying`.)
- **done** — CI reported a successful apply (with `outputs`).
- **failed** — CI reported a failed apply.
- **rejected** — the PR was closed **without** merging (a human declined it), reported by an
  on-close CI job (`pull_request: closed` fires regardless of merge; the job checks
  `merged == false`); or the request was cancelled by an operator.

Concretely:

- **Drop the plan entirely (Phase 1):** remove `get_plan`, the `GET .../steps/{n}/plan` route, the
  `pr_open → awaiting_approval` machinery, and the frontend plan view. Reviewers read the plan on
  GitHub.
- **Drop merge polling:** remove `get_pull_request_status` and `_advance_awaiting_approval` /
  `_advance_applying`. `RealGitHubClient` reduces to essentially `open_pull_request`.
- **The reconcile loop's only remaining job** is to open PRs for runnable `queued` Steps (a Step
  becomes runnable when its dependencies are `done`). It no longer reads GitHub status.
- **CI reports every terminal outcome** to the API — `done`, `failed`, and now also `rejected` —
  over the same authenticated ingress (extended from ADR-0003's `PUT .../outputs` to carry the
  outcome, not just `applied` + `outputs`).
- **Operator cancel stays API-side:** it marks the request cancelled, stops claiming, and may close
  the open PRs on GitHub; it needs no push because the API is the actor.
- **Deferred:** an "accepted"/apply-started signal (the CI pushing at the top of its apply job).
  Judged not worth the extra round trip in Phase 1; the `queued → submitted → terminal` model is
  enough.

## Consequences

- **Large, welcome deletion** across `server/orchestrator.py` (the `_advance_*` polling passes),
  `server/github_client.py` (`get_plan`, `get_pull_request_status`, `PlanNotReadyError`, the plan
  check-run handling), `server/routes/requests.py` (the `/plan` route), `server/models.py`
  (`plan_ref`, and the now-unused statuses), the frontend plan view, and their tests. Less code,
  fewer states, less to operate.
- **The app still needs `GITHUB_PAT`** — to *open* PRs (`open_pull_request`), which is unchanged.
  What goes away is the polling read path, not GitHub access entirely.
- **Rejection now depends on a CI push.** If the on-close job fails to fire or errors, a
  closed-unmerged PR would leave its Step at `submitted` indefinitely. Mitigations: keep a
  stuck-style timeout (repurpose #43's surfacing to "no terminal push received in N"), and operator
  cancel remains a manual backstop.
- **The ingress endpoint evolves.** ADR-0003's just-landed `PUT .../steps/{n}/outputs` grows an
  explicit outcome (`done` / `failed` / `rejected`) rather than only `applied` + `outputs`. This is
  a small change to a brand-new endpoint (#55/#60) and its `require_ci_principal` auth is unchanged.
- **Supersedes the polling assumptions** in architecture.md §4.2/§5/§6 and the specific
  plan-surfacing (#19) and plan/merge-decoupling (#45) work — those solved problems this ADR
  removes. The "API never runs Terraform / never parses state or logs" boundary is unchanged and,
  if anything, sharper: the API now learns *only* what CI explicitly reports.
- **The CI integration contract** (`docs/contracts/ci-integration.md`) and the fixture repo
  (`fixtures/terraform-fixture-repo/`) are rewritten to match: no `terraform-plan` check run
  requirement for state, CI pushes `done`/`failed`/`rejected`.

## Open

- Exact shape of the outcome field on the ingress (`status: done|failed|rejected` vs. keeping
  `applied: bool` plus a separate rejected path) — settled in the implementation ticket.
- Whether the dependent Step's PR is opened inline on the `done` push or on the next reconcile
  tick (either works; the tick is simpler).
