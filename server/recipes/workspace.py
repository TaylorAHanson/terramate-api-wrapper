"""The `workspace` Recipe (architecture.md §15.1, #20): "edit 2 yamls and add 2
more, in order" — create the workspace, then bind it to the metastore once its
`workspace_id` exists.

`create`'s `workspace_id` is apply-derived (architecture.md §5.1): unknown
until Terraform has actually applied. `bind` consumes it **by reference** via
`${steps.create.outputs.workspace_id}`, resolved by the reconcile loop from
Lakebase before `bind`'s PR opens (ADR-0002) — never guessed or minted early —
which is the whole reason `bind`'s PR cannot open before `create` is done.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel

from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec

_WORKSPACE_ID_PLACEHOLDER = "${steps.create.outputs.workspace_id}"


class WorkspaceParams(BaseModel):
    name: str
    metastore: str
    domain_owner: str
    groups: list[str] = []


class WorkspaceProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "workspace"` — one member of
    the `type`-discriminated Union alongside `schema` (server.routes.requests).
    """

    type: Literal["workspace"]
    params: WorkspaceParams


def render_stack(params: WorkspaceParams) -> str:
    """The new workspace's Terramate stack file — API-known up front, no
    apply-derived values involved (architecture.md §5.1)."""
    return f'stack "{params.name}" {{\n  source = "modules/workspace"\n}}\n'


def render_inputs(params: WorkspaceParams) -> str:
    """The new workspace's inputs file. `owner` is deliberately left for
    `bind`'s `set_owner_patch` to set, mirroring the recipe sketch's Step
    split even though the value is already known — see architecture.md §15.1.
    """
    return f"name: {params.name}\nmetastore: {params.metastore}\n"


def locate_metastore_binding(metastore: str) -> str:
    """The bundle file that holds `metastore`'s workspace bindings."""
    return f"stacks/metastores/{metastore}/bindings.tm.yaml"


def bind_workspace_patch(
    workspace_id: str, groups: list[str]
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that appends one workspace binding entry."""

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"workspace_id": workspace_id, "groups": list(groups)}
        return {**document, "bindings": [*document.get("bindings", []), entry]}

    return patch


def set_owner_patch(owner: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that sets the `owner` field on an existing file."""

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        return {**document, "owner": owner}

    return patch


class WorkspaceRecipe(Recipe):
    type = "workspace"

    def build(self, params: WorkspaceParams) -> Playbook:
        inputs_path = f"stacks/workspaces/{params.name}/inputs.yaml"
        return Playbook(
            steps=[
                StepSpec(
                    key="create",
                    bundle_edits=[
                        AddFile(
                            f"stacks/workspaces/{params.name}/stack.tm.hcl",
                            render_stack(params),
                        ),
                        AddFile(inputs_path, render_inputs(params)),
                    ],
                    produces=["workspace_id"],
                ),
                StepSpec(
                    key="bind",
                    depends_on=["create"],
                    consumes=[OutputRef("create", "workspace_id")],
                    bundle_edits=[
                        EditFile(
                            locate_metastore_binding(params.metastore),
                            bind_workspace_patch(_WORKSPACE_ID_PLACEHOLDER, params.groups),
                        ),
                        EditFile(inputs_path, set_owner_patch(params.domain_owner)),
                    ],
                ),
            ]
        )
