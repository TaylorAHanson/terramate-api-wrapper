"""The `workspace` Recipe: customer Foundation stack step ("first step spec for the
workspace").

Opens a pull request adding the foundation Terramate bundle instance file:
`src/configs/{name}/core_infrastructure/foundation/foundation.tm.yml`.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec

_WORKSPACE_ID_PLACEHOLDER = "${steps.create.outputs.workspace_id}"


class WorkspaceParams(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    business_domain: str = "controltower"
    business_domains: list[str] = Field(default_factory=lambda: ["controltower"])
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Optional fields for backwards compatibility with earlier prototypes
    metastore: str | None = None
    domain_owner: str | None = None
    groups: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_domains(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "business_domains" in data and data["business_domains"]:
                if "business_domain" not in data or not data["business_domain"]:
                    data["business_domain"] = data["business_domains"][0]
            elif "business_domain" in data and data["business_domain"]:
                if "business_domains" not in data or not data["business_domains"]:
                    data["business_domains"] = [data["business_domain"]]
        return data


class WorkspaceProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "workspace"` — one member of
    the `type`-discriminated Union alongside `schema` and `foundation`.
    """

    type: Literal["workspace"]
    params: WorkspaceParams


class FoundationProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "foundation"`."""

    type: Literal["foundation"]
    params: WorkspaceParams


def foundation_config_path(name: str) -> str:
    """The bundle config file for the workspace foundation stack."""
    return f"src/configs/{name}/core_infrastructure/foundation/foundation.tm.yml"


def render_foundation_config(params: WorkspaceParams) -> str:
    """The Foundation stack's Terramate bundle instance config file."""
    domains_yaml = "\n".join(f'          - "{domain}"' for domain in params.business_domains)
    return (
        "apiVersion: terramate.io/cli/v1\n"
        "kind: BundleInstance\n"
        "metadata:\n"
        "  name: foundation\n"
        f"  uuid: {params.uuid}\n"
        "spec:\n"
        '  source: "/src/bundles/core_infrastructure/foundation"\n'
        "  environments:\n"
        f"    {params.name}:\n"
        "      inputs:\n"
        "        business_domains:\n"
        f"{domains_yaml}\n"
    )


class WorkspaceRecipe(Recipe):
    type = "workspace"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="foundation",
                    bundle_edits=[
                        AddFile(
                            foundation_config_path(params.name),
                            render_foundation_config(params),
                        ),
                    ],
                ),
            ]
        )


class FoundationRecipe(Recipe):
    type = "foundation"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        return WorkspaceRecipe().build(params)


# --- Legacy / fixture helpers preserved for backwards compatibility with earlier tests ---


def render_stack(params: WorkspaceParams) -> str:
    """The new workspace's Terramate stack file — legacy fixture helper."""
    return f'stack "{params.name}" {{\n  source = "modules/workspace"\n}}\n'


def render_inputs(params: WorkspaceParams) -> str:
    """The new workspace's inputs file — legacy fixture helper."""
    return f"name: {params.name}\nmetastore: {params.metastore or ''}\n"


def locate_metastore_binding(metastore: str) -> str:
    """The bundle file that holds `metastore`'s workspace bindings — legacy fixture helper."""
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
