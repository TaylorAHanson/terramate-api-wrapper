"""The `workspace` Recipe: customer Foundation stack step ("first step spec for the
workspace").

Opens a pull request editing the foundation Terramate bundle instance file:
`src/configs/{environment}/core_infrastructure/foundation/foundation.tm.yml`
to append the requested business domain(s) under `environments.{environment}.inputs.business_domains`.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec

_WORKSPACE_ID_PLACEHOLDER = "${steps.create.outputs.workspace_id}"


class WorkspaceParams(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = ""
    environment: str = "sbx"
    business_domain: str = "controltower"
    business_domains: list[str] = Field(default_factory=lambda: ["controltower"])
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Optional fields for backwards compatibility with earlier prototypes
    metastore: str | None = None
    domain_owner: str | None = None
    groups: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bds = data.get("business_domains")
            bd = data.get("business_domain")
            name = data.get("name")

            if bds:
                if not bd:
                    data["business_domain"] = bds[0]
            elif bd:
                data["business_domains"] = [bd]
            elif name and name not in ("sbx", "sbx-test", "workspace"):
                data["business_domain"] = name
                data["business_domains"] = [name]
            else:
                data["business_domain"] = "controltower"
                data["business_domains"] = ["controltower"]

            if not data.get("name"):
                data["name"] = data.get("business_domain", "workspace")

            if not data.get("environment"):
                data["environment"] = "sbx"
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


def foundation_config_path(environment: str = "sbx") -> str:
    """The bundle config file for the workspace foundation stack."""
    return f"src/configs/{environment}/core_infrastructure/foundation/foundation.tm.yml"


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
        f"    {params.environment}:\n"
        "      inputs:\n"
        "        business_domains:\n"
        f"{domains_yaml}\n"
    )


def add_business_domain_patch(
    domains: list[str],
    environment: str = "sbx",
    default_uuid: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that appends business domain(s) under
    environments.{environment}.inputs.business_domains without modifying existing domains.
    """

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        doc = dict(document)
        if not doc:
            doc = {
                "apiVersion": "terramate.io/cli/v1",
                "kind": "BundleInstance",
                "metadata": {
                    "name": "foundation",
                    "uuid": default_uuid or str(uuid.uuid4()),
                },
                "spec": {
                    "source": "/src/bundles/core_infrastructure/foundation",
                    "environments": {
                        environment: {
                            "inputs": {
                                "business_domains": ["controltower"],
                            }
                        }
                    },
                },
            }

        spec = doc.setdefault("spec", {})
        envs = spec.setdefault("environments", {})
        env_config = envs.setdefault(environment, {})
        inputs = env_config.setdefault("inputs", {})
        existing_domains = inputs.setdefault("business_domains", [])

        for domain in domains:
            if domain not in existing_domains:
                existing_domains.append(domain)

        return doc

    return patch


class WorkspaceRecipe(Recipe):
    type = "workspace"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="foundation",
                    bundle_edits=[
                        EditFile(
                            foundation_config_path(params.environment),
                            add_business_domain_patch(
                                domains=params.business_domains,
                                environment=params.environment,
                                default_uuid=params.uuid,
                            ),
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
