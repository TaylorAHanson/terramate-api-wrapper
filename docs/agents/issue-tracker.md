# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments`.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Blocking edges — inline prose ONLY (repo override)

**This repo does NOT use GitHub's native issue dependencies. Do not create them.**

This deliberately overrides the default wayfinder/tally-ho convention (which uses
native issue dependencies as the canonical, frontier-gating representation). In
this repo, blocking is expressed **only** as a prose line at the top or bottom of
the child issue's body:

```
Blocked by: #<n>, #<n>
```

- Never call the `dependencies/blocked_by` API or add a native dependency edge for any reason.
- Never suggest adding one as an "improvement."
- Consequence, understood and accepted: the automated frontier query does **not**
  read this prose, so it will **not** stop a blocked ticket from being claimed or
  spawned. Ordering is gated **manually** — a human decides when to spawn/land a
  ticket whose blockers aren't yet merged. The prose line documents intent for
  humans and agents reading the issue; it is not an automated gate.

## Labels

Implementable, agent-grabbable tickets carry both `wayfinder:task` and `ready-for-agent`.
Other `wayfinder:*` labels (`map`, `grilling`, `prototype`, `research`) mark non-task issues.

## When a skill says "publish to the issue tracker"

Create a GitHub issue, and express any blocking edge as the inline `Blocked by:` prose above.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
