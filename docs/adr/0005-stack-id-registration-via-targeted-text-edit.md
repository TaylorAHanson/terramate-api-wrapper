# 0005 — Register stack ids via a targeted text edit of the env config

- Status: Accepted
- Date: 2026-10-05
- Narrows the "bundle edits are parse → mutate → serialize, never text munging" rule (`EditFile`, architecture.md §15.1) for one file.

## Context

Every stack the terramate repo deploys needs an entry in the stack ids block (`globals "stack_ids" "<env>" { ... }`, or a `stack_ids = { ... }` attribute) of
`src/configs/<env>/<env>_config.tm.hcl`; the repo uses it as the prefix for that stack's state
files. So every Recipe that creates a stack (workspace, workspace_folder, unity_catalog,
unity_catalog_schema, and whatever comes next) must also add `<stack_name> = "<uuid>"` there, in
the same PR as the stack's YAML.

That file is HCL, not YAML. There is no maintained Python library that round-trips HCL
(parse → mutate → serialize) while preserving comments, ordering, and the `terraform fmt` alignment
reviewers expect; `python-hcl2` parses but cannot write the file back faithfully. The block is
also a deliberately simple, flat map of `key = "uuid"` lines grouped under
`# <family>/<unit> stacks: "<naming>"` comments.

## Decision

Add a third bundle-edit kind, **`EditText(path, patch: str -> str)`**, alongside `AddFile` and
`EditFile`. It is used only where no structured serializer exists, and its patch must change only
the lines it adds or re-aligns.

The single use today is `server/recipes/stack_ids.py`'s `register_stack_id`:

- appends the entry to its stack family's commented group (adding the group comment if absent),
- re-aligns that group's `=` column the way `terraform fmt` would, leaving every other line
  byte-for-byte unchanged,
- is a no-op when the key is already present (an existing stack, or a retried PR),
- fails loudly if the file has no `stack_ids` block, rather than inventing one.

The stack's uuid is minted once at request time and persisted in the request's `params`, so the
YAML `metadata.uuid` and the `stack_ids` value are the same and stable across Playbook rebuilds.

## Consequences

- Recipes that create stacks get registration by adding one `stack_ids.register_stack_id(...)`
  edit to their Step; the new-Type steps in `AGENTS.md` cover it.
- Every stack-creating PR touches the same block of the same file. Steps within one request are
  serial, so they never collide, but **two concurrent requests in the same environment will
  conflict on merge** (both append to the same group). The second PR must be rebased or
  re-opened. Revisit if concurrent requests per environment become common (e.g. a per-stack
  generated file the config `tm_merge`s, owned by the terramate repo).
- Text edits are reviewed by eye like any other diff; the unit tests in `tests/unit/test_stack_ids.py`
  pin the exact added/re-aligned lines.
