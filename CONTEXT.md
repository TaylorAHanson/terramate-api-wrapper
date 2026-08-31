# Context — terramate-api-wrapper

A versioned abstraction API sitting between a self-service (agentic) application and
the Terramate/Terraform codebase that actually provisions Databricks resources. The
self-service side POSTs a small, stable request; the API encodes the "tribal knowledge"
of editing/adding the Terramate catalyst bundle files on a branch, opening a pull
request, and ordering those PRs correctly (passing outputs between them). Either
neighbouring system can change without breaking the other.

**The API never runs Terraform itself.** It opens PRs; the terramate repo's existing
**GitHub Actions** run plan/apply, a human approves/merges the PR, and the API polls
GitHub for results. Terraform execution and state are out of scope — they belong to the
repo's CI. The API is a PR-orchestration + polling + output-capture engine.

Physical shape: Databricks App (FastAPI + React), Lakebase (managed Postgres) for
durable storage. No durable filesystem storage (bundle edits are made via the GitHub
API / an ephemeral checkout, never persisted locally).

## Glossary

<!-- Glossary only. No implementation details, no decisions — those live in docs/adr/. -->

### ProvisioningRequest
The unit a client submits via `POST /v1/requests`. One high-level provisioning intent —
"provision a workspace", "create a catalog" — carrying a resource **type** plus that
type's parameters. There is **no common envelope**: the parameter set is entirely
type-specific (a workspace's params differ from a schema's). Expands into exactly one
Plan. Ordering and value-passing are always *within* a single ProvisioningRequest (v1);
cross-request dependencies are out of scope.

### Type
The kind of resource a ProvisioningRequest asks for — **`catalog`** (a Unity Catalog
catalog) is itself just one type, alongside `workspace`, `schema`, `service_principal`, …
The menu will eventually carry 50+ types; each type's parameter schema is published in the
API's **OpenAPI spec** as a discriminated union on `type` (there is no separate `/types`
endpoint). Each type is backed by a **Recipe** — not a declarative definition. (Considered
and rejected for now: a data-driven registry; the per-type git operations vary too much to
model declaratively. Simple first, not perfect.)

### Recipe
The per-type tribal knowledge, expressed imperatively in code: the specific, ordered git
operations that realise one type in the cloned terramate repo — e.g. a workspace is "edit
2 yamls and add 2 yamls, in order"; a catalog is "find the right yaml, then edit it".
A Recipe both mutates the bundle files and yields the Plan of ordered Steps to run.

### Plan
The ordered DAG of Steps a ProvisioningRequest expands into. (Distinct from a Terraform "plan"
— when the Terraform sense is meant, say "terraform plan".)

### Step
One node in a Plan, realised as a **pull request** the API opens against the terramate
repo. GitHub Actions runs plan/apply for it; a human approves and merges the PR (approval
gate). The API waits for the Actions apply to finish, captures the Step's Outputs, then
proceeds to the next Step. May depend on earlier Steps and consume their Outputs.

### Bundle
The set of Terramate catalyst bundle files (~4 yaml files) for one resource, edited or
added in the cloned terramate repo. The concrete unit the API generates.

### Output
A value produced by an applied Step (e.g. an id emitted by terraform) that a later Step
in the same Plan consumes as an input. The reason ordering matters. Captured by the API
by **pulling** it from the Step's Actions run (a structured check-run output / PR comment
in an agreed contract), since the API never reads terraform state directly. Requires the
terramate repo's Actions to emit outputs in that contract.

### Version
A `/vN` of the API, **selected by the client via the URL path**. Pins the way the API
interacts with the Terramate bundles, so the self-service app, this API, and
Terramate/Terraform can change asynchronously — when Terramate changes, a new version is
cut over to while old versions keep working. (Open/fog: possible *version affinity* — a
resource created under v1 stays pinned to v1 for later changes; tied to whether day-2
modifications exist at all.)
