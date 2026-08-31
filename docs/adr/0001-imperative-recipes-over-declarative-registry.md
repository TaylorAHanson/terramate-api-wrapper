# 0001 — Imperative Recipes over a declarative type registry

- Status: Accepted
- Date: 2026-08-31

## Context

The API must translate a `ProvisioningRequest` of some `type` (workspace, catalog,
schema, service_principal, …) into the concrete edits to the Terramate catalyst bundle
files and the ordered plan/apply operations that realise it. The menu of types is expected
to grow to 50+ over time.

The obvious "scale-first" design is a **data-driven registry**: each type is a declarative
definition (bundle-file templates + a parameter schema + ordering rules) interpreted by a
generic engine, so adding type #51 is authoring data, not shipping code.

In practice the per-type git operations vary too much to model declaratively and cleanly:

- A workspace is "edit 2 yamls and add 2 yamls, in order."
- A catalog is "find the right yaml, then edit it."
- Other types have their own idiosyncratic variations, including lookups against existing
  repo content to decide *which* file to touch.

Forcing all of that into a declarative schema would be a large, speculative abstraction
built before we have seen enough real recipes to know its shape.

## Decision

Back each type with an **imperative Recipe in code** — a per-type unit that knows the
specific, ordered git operations for that type and yields the Plan of ordered Steps.
There is a shared Recipe *interface*, but the body of each Recipe is ordinary code, not a
declarative definition. We optimise for **simple first, not perfect**.

## Consequences

- Adding a new type means writing (and deploying) a new Recipe — not editing data. This is
  acceptable at the current scale and until the recipes reveal a stable common shape.
- The variation between types (multi-file edits, "find the right yaml" lookups) is
  expressed naturally in code rather than fought into a schema.
- **Revisit trigger**: once enough Recipes exist to expose a genuine common pattern — or
  when the deploy-per-type cost becomes the bottleneck against the 50+ goal — reconsider
  extracting a declarative registry from the recipes we actually have.
- The Recipe interface itself is a distinct design task (see the wayfinder map).
