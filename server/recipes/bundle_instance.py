"""Shared create-or-merge patch for a Terramate `BundleInstance` stack file.

Every per-stack config in the terramate repo has the same shape:

    apiVersion: terramate.io/cli/v1
    kind: BundleInstance
    metadata:
      name: <stack name>
      uuid: "<uuid>"
    spec:
      source: "/src/bundles/<family>/<unit>"
    environments:
      <env>:
        inputs:
          ...

`bundle_instance_patch` creates that file when it doesn't exist yet, and when
it does, only fills in missing inputs and appends missing list items — it
never rewrites a value that's already there.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from server.yaml_util import QuotedStr


def bundle_instance_patch(
    *,
    name: str,
    stack_uuid: str,
    source: str,
    environment: str,
    inputs: dict[str, Any],
    append_to: dict[str, list[str]] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """`inputs` are set only if absent; each `append_to` list input gets any
    of its items it doesn't already contain, in order."""

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        doc = copy.deepcopy(document)
        if not doc:
            doc = {
                "apiVersion": "terramate.io/cli/v1",
                "kind": "BundleInstance",
                "metadata": {"name": name, "uuid": QuotedStr(stack_uuid)},
                "spec": {"source": QuotedStr(source)},
                "environments": {},
            }

        env_inputs = _child(_child(_child(doc, "environments"), environment), "inputs")
        for key, value in inputs.items():
            env_inputs.setdefault(key, _quoted(value))
        for key, items in (append_to or {}).items():
            existing = env_inputs.get(key)
            if not isinstance(existing, list):
                existing = env_inputs[key] = []
            for item in items:
                if item not in existing:
                    existing.append(QuotedStr(item))
        return doc

    return patch


def _child(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(mapping.get(key), dict):
        mapping[key] = {}
    return mapping[key]


def _quoted(value: Any) -> Any:
    if isinstance(value, str):
        return QuotedStr(value)
    if isinstance(value, list):
        return [_quoted(v) for v in value]
    if isinstance(value, dict):
        return {k: _quoted(v) for k, v in value.items()}
    return value
