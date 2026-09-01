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

## Blocking edges — native GitHub dependencies ONLY

**Always express blocking with GitHub's native issue dependencies. Never use a prose
`Blocked by:` line.** Native dependencies are the canonical, UI-visible, frontier-gating
representation that `/wayfinder` and tally-ho actually read.

Add an edge (child is blocked by blocker):

```
# resolve the BLOCKER's numeric database id (NOT the #number, NOT the node_id):
blocker_id=$(gh api repos/<owner>/<repo>/issues/<blocker-number> --jq .id)
gh api --method POST repos/<owner>/<repo>/issues/<child-number>/dependencies/blocked_by \
  -F issue_id="$blocker_id"
```

- GitHub reports open blockers via `issue_dependencies_summary.blocked_by` — that count is the live gate.
- A ticket is unblocked when every blocker is **closed**.
- The frontier query drops any issue with `issue_dependencies_summary.blocked_by > 0` (or an assignee); first in map order wins.
- Do **not** add a prose `Blocked by:` line. If you find one on an existing issue, replace it with a native dependency.

## Labels

Implementable, agent-grabbable tickets carry both `wayfinder:task` and `ready-for-agent`.
Other `wayfinder:*` labels (`map`, `grilling`, `prototype`, `research`) mark non-task issues.

## When a skill says "publish to the issue tracker"

Create a GitHub issue, and express any blocking edge as a native `blocked_by` dependency (above).

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
