# Self-service integration contract — Self-service app ↔ Provisioning API (ADR-0004)

**Audience:** whoever owns the self-service app (SSC) that submits provisioning
requests on a user's behalf.
**Purpose:** the exact, minimal contract your app must satisfy to drive the
provisioning API (`terramate-api-wrapper`) end to end — what you send, what you
poll, and how you know a request is done.
**Sibling contract:** the CI/GitHub-Actions side is
[`ci-integration.md`](ci-integration.md).

The single most important boundary: **the provisioning API is a request intake +
status oracle, not a Terraform runner and not an approval surface.** You `POST`
one provisioning request; the API expands it into one or more ordered Steps and
opens **one GitHub PR per Step**. A **human reviews and merges (or closes) each
PR on GitHub** — that is the approval gate, and it lives entirely on GitHub, not
in this API (ADR-0004). CI reports each Step's terminal outcome back to the API.
Your app's only job after submitting is to **poll for the request's terminal
status** and point the user at the open PR when there is one to act on.

---

## The loop

```mermaid
sequenceDiagram
    participant SSC as Self-service app (you)
    participant API as Provisioning API
    participant Repo as Terramate repo (GitHub)
    participant CI as GitHub Actions
    SSC->>API: POST /v1/requests {type, params} + Idempotency-Key
    API-->>SSC: 202 {request_id, status: "pending"}
    API->>Repo: open PR for each runnable Step
    Note over SSC,API: you poll GET /v1/requests/{id}
    SSC->>API: GET /v1/requests/{id}
    API-->>SSC: step "submitted" + pr_url
    Note over Repo: a human reviews the plan on the PR & merges — or closes it
    Repo->>CI: pull_request (merged / closed)
    CI->>API: PUT .../outputs {status: done|failed|rejected}
    API->>API: Step terminal → next Step's PR opens, or request terminal
    SSC->>API: GET /v1/requests/{id}
    API-->>SSC: request status: succeeded | failed | cancelled
```

Your actions are the `POST` and the polling `GET`s. Everything between "PR
opened" and "request terminal" is a human on GitHub plus a CI push — you observe
it, you don't drive it.

---

## Endpoints you use

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/requests` | Submit a provisioning request. |
| `GET`  | `/v1/requests/{id}` | Poll request + per-Step status (your done/not-done signal). |
| `GET`  | `/v1/requests/{id}/steps/{ordinal}` | One Step's detail (optional; the same fields the list gives). |
| `POST` | `/v1/requests/{id}/cancel` | Halt an in-flight request. |
| `GET`  | `/v1/health` | Liveness check. |

### Endpoints you must NOT call

- **`PUT /v1/requests/{id}/steps/{ordinal}/outputs`** — the CI-only outcome
  ingress. It is gated on the `CI_PRINCIPALS` allowlist; your app's principal is
  not (and must not be) on it. See [`ci-integration.md`](ci-integration.md).
- **`GET`/`POST /v1/admin/intake-gate`** — operator-only, gated on
  `ADMIN_PRINCIPALS`. Do **not** pre-check the gate before submitting; you'll get
  `403` unless your principal is an admin. Instead, just submit and treat a `503`
  on `POST /v1/requests` as "intake closed" (see below).

---

## 1. Submit a request

```
POST /v1/requests
Content-Type: application/json
Idempotency-Key: <stable client-generated key>
Authorization: Bearer <token>   # see Auth below
```

The `Idempotency-Key` header is **required**. Use a stable key per logical
request (e.g. a UUID you persist with the user's intent). A repeat `POST` with a
key already seen is a no-op that returns the **original** request's
`{request_id, status}` — safe to retry on transient network failure, and the
replay is resolved **before** the intake gate is checked (a replay of an
already-accepted request succeeds even if intake later closed).

The body is a **discriminated union on `type`** (also published live at
`/openapi.json`):

```json
{ "type": "<type>", "params": { ... } }
```

### Request types at a glance

| `type` | Required params | What it provisions | PRs |
|---|---|---|---|
| `workspace` (aliases `foundation`, `network_foundation`) | none (`business_domain` defaults to `"controltower"`, so always send it) | A business domain's network foundation entry, then its workspace stack | 2, sequential |
| `workspace_folder` | `business_domain` | The domain's workspace folders | 1 |
| `unity_catalog` | `business_domain` | The domain's Unity Catalog catalogs | 1 |
| `unity_catalog_schema` | `business_domain` | Schemas in one of the domain's catalogs | 1 |
| `schema` | `catalog`, `name`, `owner` | Legacy prototype type (fixture repo layout); not for the domain stacks above | 1 |

Every optional param has a default (tables below); `environment` defaults to
`"sbx"` everywhere. Every Step that creates a new stack also registers
`<stack_name> = "<uuid>"` in the `stack_ids` block of
`src/configs/{environment}/{environment}_config.tm.hcl`, in the same PR
(ADR-0005).

### Building self-service tools from these types

- **Order across tools.** Each type is an independent request; the API does
  not enforce ordering *between* requests. Run them in dependency order, and
  wait for each request to reach `succeeded` before submitting the next:
  1. `workspace`
  2. `workspace_folder` and `unity_catalog` (either order, both need the workspace)
  3. `unity_catalog_schema` (needs its catalog from `unity_catalog`)
- **Never send uuids.** `uuid`, `stack_uuid`, `stack_uuids`, and
  `business_domain_uuid` are generated and persisted by the API; leave them
  out of tool schemas.
- **`unity_catalog_schema`: pick the catalog one way.** Send `catalog_suffix`
  (the suffix it was created with) *or* `catalog_name` (the full name), not
  both; omit both for the domain's default catalog. Treat `schemas` as
  required in your tool — the API accepts an empty list, but then the PR only
  creates an empty schema stack.
- **Re-running is additive.** Submitting `workspace_folder`, `unity_catalog`,
  or `unity_catalog_schema` again for an existing stack only appends folders /
  suffixes / schemas that aren't already listed; it never removes or renames.
- **Names are derived, not passed.** `workspace_name` defaults to
  `{domain}_ws_{environment}`; catalogs are `{domain}_{environment}` and
  `{domain}_{suffix}_{environment}`. Override `workspace_name` only if the
  domain's workspace was named differently.

### `type: "schema"` — add a schema to an existing catalog

```json
{
  "type": "schema",
  "params": {
    "catalog": "main",
    "name": "analytics",
    "owner": "data-eng@acme.com",
    "comment": "optional, may be omitted or null"
  }
}
```

| Param | Required | Meaning |
|---|---|---|
| `catalog` | yes | Existing catalog to add the schema to. |
| `name` | yes | New schema name. |
| `owner` | yes | Schema owner principal. |
| `comment` | no | Free-text comment; omit or `null` for none. |

One Step (`add-schema`), no apply-derived outputs.

### `type: "workspace"` (or `type: "foundation"`, `type: "network_foundation"`) — provision a domain in the Network Foundation stack

Registers a new business domain (e.g. `finance`) under the Terramate bundle configuration for the target environment (`sbx`). Opens a pull request editing `src/configs/{environment}/core_infrastructure/network_foundation/network_foundation.tm.yml` to append the business domain and its `subnet_size` under `inputs.business_domains`.

```json
{
  "type": "workspace",
  "params": {
    "business_domain": "finance",
    "subnet_size": "small",
    "environment": "sbx"
  }
}
```

| Param | Required | Default | Meaning |
|---|---|---|---|
| `business_domain` | no, but always send it | `"controltower"` | Business domain to add to `inputs.business_domains`. |
| `subnet_size` | no | `"small"` | Subnet size allocation: `"small"`, `"medium"`, or `"large"`. |
| `environment` | no | `"sbx"` | Target environment key in the bundle configuration. |
| `business_domains` | no | `[business_domain]` | Several domains in one request, instead of `business_domain`. |
| `name` | no | `business_domain` | Display label for the request; not written to the repo. |

Legacy/ignored params, accepted for backward compatibility only — leave them
out of tool schemas: `metastore`, `domain_owner`, `groups`, `uuid`,
`business_domain_uuid`, `stack_uuids`.

Two Steps (executed sequentially as separate pull requests):
1. `network_foundation` — opens a PR editing `src/configs/{environment}/core_infrastructure/network_foundation/network_foundation.tm.yml` to append the business domain with its `subnet_size` (defaults to `"small"`).
2. `business_domain` — depends on `network_foundation`. Once PR 1 is merged and applied, opens a PR creating (or updating if adding an environment) the workspace stack `src/configs/{environment}/domain_stacks/business_domain/workspace/{domain}/{domain}_workspace.tm.yml`, and registering `{domain}_workspace` in `stack_ids`.

Neither step requires apply-derived outputs.

### `type: "workspace_folder"` — a domain's workspace folders

```json
{ "type": "workspace_folder", "params": { "business_domain": "finance" } }
```

| Param | Required | Default | Meaning |
|---|---|---|---|
| `business_domain` | yes | — | The domain whose workspace gets the folders. |
| `environment` | no | `"sbx"` | Target environment. |
| `workspace_name` | no | `"{domain}_ws_{environment}"` | Workspace the folders live in. |
| `folder_names` | no | the standard set: `{domain}_assets/ai_apps`, `…/ai_ml`, `…/data_aibi`, `…/data_apps`, `…/data_deng`, `{domain}_aibi_genie`, `{domain}_aibi_dashboards` | Folders to create; on an existing stack, any not already listed are appended. |

One Step (`workspace_folder`): creates or appends to `src/configs/{environment}/domain_stacks/business_domain/workspace_folder/{domain}/{domain}_workspace_folder.tm.yml` and registers `{domain}_workspace_folder` in `stack_ids`.

### `type: "unity_catalog"` — a domain's catalogs

```json
{ "type": "unity_catalog", "params": { "business_domain": "finance", "catalog_suffixes": ["ai"] } }
```

| Param | Required | Default | Meaning |
|---|---|---|---|
| `business_domain` | yes | — | The domain that owns the catalogs. |
| `environment` | no | `"sbx"` | Target environment. |
| `workspace_name` | no | `"{domain}_ws_{environment}"` | Workspace the catalogs are bound to. |
| `catalog_suffixes` | no | `[]` | Extra catalogs. The default catalog `{domain}_{environment}` is always created; each suffix adds `{domain}_{suffix}_{environment}`. On an existing stack, new suffixes are appended. |

One Step (`unity_catalog`): creates or appends to `src/configs/{environment}/domain_stacks/data_domain/unity_catalog/{domain}/{domain}_unity_catalog.tm.yml` and registers `{domain}_unity_catalog` in `stack_ids` (a no-op if the stack already exists).

### `type: "unity_catalog_schema"` — schemas in one catalog

```json
{ "type": "unity_catalog_schema", "params": { "business_domain": "finance", "catalog_suffix": "ai", "schemas": ["bronze", "silver"] } }
```

| Param | Required | Default | Meaning |
|---|---|---|---|
| `business_domain` | yes | — | The domain that owns the catalog. |
| `environment` | no | `"sbx"` | Target environment. |
| `workspace_name` | no | `"{domain}_ws_{environment}"` | Workspace the catalog is bound to. |
| `catalog_suffix` | no | `null` | Which catalog: omit for the default `{domain}_{environment}`, or the suffix it was created with. |
| `catalog_name` | no | derived from the above | Full catalog name; overrides `catalog_suffix`. Send one or the other, not both. |
| `schemas` | no (treat as required in your tool) | `[]` | Schemas to create; on an existing stack, any not already listed are appended. |

One Step (`unity_catalog_schema`): creates or appends to `src/configs/{environment}/domain_stacks/data_domain/unity_catalog_schema/{domain}/{catalog_name}_unity_catalog_schema.tm.yml` and registers `{catalog_name}_unity_catalog_schema` in `stack_ids` (a no-op if the stack already exists).

### Responses

| Status | Body | Your action |
|---|---|---|
| `202` | `{ "request_id": "<uuid>", "status": "pending" }` | Persist `request_id`; start polling. |
| `401` | `{ "detail": "No resolvable caller identity" }` | Your token didn't resolve to a forwarded identity. Fix auth. **Permanent.** |
| `422` | validation error | Missing `Idempotency-Key`, unknown `type`, or bad `params` (e.g. no `business_domain` on `workspace_folder` / `unity_catalog` / `unity_catalog_schema`). **Permanent** — do not retry unchanged. |
| `503` | `{ "detail": "Intake is currently disabled" }` | The global intake gate is closed. **Permanent** for this attempt — surface to the user; a retry only succeeds after an operator reopens intake. |

---

## 2. Poll for status — the done/not-done contract

```
GET /v1/requests/{request_id}
```

Returns the full request with its Steps:

```json
{
  "id": "…", "type": "workspace", "params": { "business_domain": "finance", "subnet_size": "small", "name": "finance", "…": "…" },
  "version": "v1", "requester": "…",
  "status": "in_progress",
  "created_at": "…", "updated_at": "…",
  "steps": [
    {
      "ordinal": 0, "key": "network_foundation", "status": "submitted",
      "pr_number": 42, "pr_url": "https://github.com/…/pull/42",
      "depends_on": [], "stuck": false, "status_changed_at": "…"
    },
    {
      "ordinal": 1, "key": "business_domain", "status": "queued",
      "pr_number": null, "pr_url": null,
      "depends_on": ["<step id>"], "stuck": false, "status_changed_at": "…"
    }
  ]
}
```

`404` if `request_id` is unknown. `params` echoes what you sent plus the
defaults and generated uuids the API filled in. `depends_on` holds internal
Step ids, not Step keys — use `ordinal`/`key` to identify Steps, and don't
key logic on `depends_on`.

### The single indicator you need: `status` at the request level

| Request `status` | Meaning | Terminal? |
|---|---|---|
| `pending` | Accepted; first PR(s) not opened yet. | no |
| `in_progress` | At least one Step is moving. | no |
| `succeeded` | **Done — all Steps applied.** | **yes** |
| `failed` | **Not done — a Step failed or its PR was rejected.** | **yes** |
| `cancelled` | **Not done — an operator/you cancelled it.** | **yes** |

**Poll `GET /v1/requests/{id}` until `status` is one of `succeeded` / `failed` /
`cancelled`.** That is your done/not-done signal — `succeeded` is done-good,
`failed`/`cancelled` are done-not-good. You do not need to read outputs; the API
does not return apply-derived values (workspace ids, etc.) on this path, by
design — the contract is completion, not values.

### Per-Step detail (for progress + the approval seam)

Each Step's `status`:

| Step `status` | Meaning |
|---|---|
| `queued` | Dependencies not yet `done`, waiting its turn behind another request's open PR (see below), or intake gated; no PR yet. |
| `submitted` | **PR is open — a human must review the plan and merge (approve) or close (reject) it on GitHub.** |
| `done` | Applied successfully. |
| `failed` | Applied and failed. |
| `rejected` | PR closed without merging (a human declined it). |

- **`pr_url` is your approval seam.** While a Step is `submitted`, surface its
  `pr_url` to the user: *"Review the plan and merge (or close) this PR to
  approve (or reject)."* There is **no approve-via-API** call — approval is the
  GitHub merge. `pr_url`/`pr_number` are `null` only before the PR opens
  (`queued`).
- **Requests take turns.** `workspace`, `workspace_folder`, `unity_catalog`
  and `unity_catalog_schema` share one queue: across all of them, only one PR
  is open at a time, and requests are served in the order they were created.
  A request can sit at `queued` with no PR while an earlier request's PR
  waits for review. Surface that as "waiting behind earlier requests", not an
  error. (`schema` doesn't queue.)
- **`stuck: true`** means the Step has sat at `submitted` past the API's
  threshold — CI's terminal push never arrived. Surface it as "waiting longer
  than expected; may need operator attention."
- `status_changed_at` is when the Step entered its current status (useful for
  "waiting since…").

---

## 3. Cancel a request

```
POST /v1/requests/{request_id}/cancel
```

| Status | Body / meaning |
|---|---|
| `200` | `{ "request_id", "status": "cancelled" }`. Already-`cancelled` returns `200` too (idempotent). |
| `409` | Request already reached a **different** terminal state (`succeeded`/`failed`) — can't be undone by cancelling. |
| `404` | Unknown `request_id`. |

Cancel stops the reconcile loop from advancing the request further; Steps that
already applied stay applied (halt, no rollback).

---

## Auth (M2M service principal)

The API is deployed as a **Databricks App**, behind the Databricks Apps OAuth
proxy. Your app authenticates as a **Databricks service principal**, exactly like
the CI side:

1. Mint a workspace OAuth token with the SP's client-credentials grant:
   ```
   POST {DATABRICKS_HOST}/oidc/v1/token
   Authorization: Basic base64(client_id:client_secret)
   Content-Type: application/x-www-form-urlencoded
   grant_type=client_credentials&scope=all-apis
   ```
   → `access_token`.
2. Send it as `Authorization: Bearer <access_token>` on every call.

The operator must grant your SP **can use** on the App. (Unlike CI, your SP does
**not** go on `CI_PRINCIPALS` — that allowlist is only for the outcome ingress
you never call.)

### Requester attribution — read this

The proxy authenticates the token and stamps the caller's identity onto
`X-Forwarded-Email` / `X-Forwarded-User` before the request reaches the app,
overwriting any such header you send. `POST /v1/requests` records that identity
as the request's `requester`.

Because you call **M2M**, the recorded `requester` is **your app's service
principal — not the human end user**. There is **no** client header to override
this (`X-Requester` is not read; the proxy owns forwarded identity). If you need
the human attributed for audit, that requires an API change on our side (an
explicit actor field in the request body), not a header — raise it with us.

`GET` and `cancel` have no app-level identity requirement beyond passing the
proxy, so any valid SP token reaches them.

---

## What changed from the earlier design (ADR-0004)

If your client was written against an earlier draft, three things are gone:

- **No plan endpoint.** `GET /v1/requests/{id}/steps/{ordinal}/plan` was
  **removed** — a call now `404`s. The API never surfaces the Terraform plan; a
  reviewer reads it on the GitHub PR. Delete any "fetch plan" call and any
  `409`-plan-pending handling. Replace that UX with the `pr_url` approval seam
  above.
- **No plan/merge Step states.** The old `pr_open` / `awaiting_approval` /
  `applying` Step statuses no longer exist. The lifecycle is
  `queued → submitted → {done|failed|rejected}` (see the table above). Any
  mapping keyed on the old states must be rewritten.
- **Outcome, not values.** Completion is expressed purely through the request/
  Step `status` fields. The read path returns no apply-derived outputs.

## What you must NOT rely on

The API never runs Terraform, never polls GitHub, never surfaces a plan, and
never returns apply-derived output values on the read path. If you need a
signal, it must be one of the `status` fields above. Approval is a human GitHub
merge, observed via `pr_url` + Step `status` — not an API action you can take.
