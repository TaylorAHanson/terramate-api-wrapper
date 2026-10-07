# 0006 — A FIFO serial lane for non-parallel Recipes

- Status: Accepted
- Date: 2026-10-07
- Resolves the concurrent-request merge conflict ADR-0005 left open.

## Context

Steps within one request already open one PR at a time: a Step waits until its dependencies are
`done`. Across requests there was no ordering. The reconcile loop opened every runnable Step's PR in
the same tick, ordered only by Step ordinal.

Every stack-creating Type edits the same `stack_ids` block in `<env>_config.tm.hcl` (ADR-0005), and
`workspace` also edits the shared network foundation file. Two of those PRs open at once each branch
off the same base commit, so whichever merges second conflicts and has to be rebased by hand.

## Decision

Each Recipe declares **`parallel: bool`** (`server/recipes/framework.py`). It defaults to `False`,
so a new Type is serial unless its author opts out.

- **Serial Types share one lane.** At most one Step PR of *any* non-parallel Type is open
  (`submitted`) at a time. A queued serial Step only opens once that PR's Step reaches a terminal
  status (`done`, `failed`, `rejected`) or its request is halted (failed/cancelled).
- **FIFO.** Queued Steps are considered oldest request first (`created_at`, then id), then by Step
  ordinal. An older multi-Step request therefore keeps its turn between its own Steps; a newer
  request doesn't slip in while the older one's next Step is waiting to open.
- **Parallel Types bypass the lane** and open as soon as their dependencies are `done`, alongside
  whatever serial PR is open. Only `schema` is parallel today.
- **Replica-safe.** The lane check runs under a transaction-scoped Postgres advisory lock
  (`pg_advisory_xact_lock`), so two reconcile drivers can't both see the lane free and both open a
  serial PR. `SELECT ... FOR UPDATE SKIP LOCKED` alone doesn't cover this, because the two drivers
  could be claiming *different* Steps.

The lane is global rather than per environment. The serial Types also touch files that aren't
environment-scoped, and one global lane is the simplest rule to reason about.

## Consequences

- Concurrent stack-creating requests no longer conflict on merge. The cost is throughput: serial
  requests complete one PR (plan, review, merge, apply) at a time.
- A serial PR that is never merged or closed holds up every serial request behind it. It is
  surfaced by the existing stuck-Step flag (ADR-0004). An operator unblocks the lane by closing the
  PR (CI reports `rejected`) or cancelling the request.
- Waiting serial Steps stay `queued`; there's no separate status for "waiting for its turn".
- A Type that only touches its own files can set `parallel = True` to skip the queue.
