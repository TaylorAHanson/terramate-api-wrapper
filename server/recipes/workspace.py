"""The `workspace` Recipe: customer Network Foundation stack step ("first step spec for the
workspace").

Opens a pull request editing the network foundation Terramate bundle instance file:
`src/configs/{environment}/core_infrastructure/network_foundation/network_foundation.tm.yml`
to append the requested business domain(s) with subnet_size under `environments.{environment}.inputs.business_domains`.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec
from server.yaml_util import QuotedStr, dump_yaml

_WORKSPACE_ID_PLACEHOLDER = "${steps.create.outputs.workspace_id}"


class WorkspaceParams(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = ""
    environment: str = "sbx"
    business_domain: str = "controltower"
    business_domains: list[str] = Field(default_factory=lambda: ["controltower"])
    subnet_size: Literal["small", "medium", "large"] | str = "small"
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    business_domain_uuid: str | None = None
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

            if not data.get("subnet_size"):
                data["subnet_size"] = "small"
        return data


class WorkspaceProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "workspace"` — one member of
    the `type`-discriminated Union alongside `schema`, `foundation`, and `network_foundation`.
    """

    type: Literal["workspace"]
    params: WorkspaceParams


class FoundationProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "foundation"`."""

    type: Literal["foundation"]
    params: WorkspaceParams


class NetworkFoundationProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "network_foundation"`."""

    type: Literal["network_foundation"]
    params: WorkspaceParams


def network_foundation_config_path(environment: str = "sbx") -> str:
    """The bundle config file for the workspace network foundation stack."""
    return f"src/configs/{environment}/core_infrastructure/network_foundation/network_foundation.tm.yml"


foundation_config_path = network_foundation_config_path


def render_network_foundation_config(params: WorkspaceParams) -> str:
    """The Network Foundation stack's Terramate bundle instance config file."""
    domains_yaml = "\n".join(
        f'        {domain}:\n          subnet_size: "{params.subnet_size}"'
        for domain in params.business_domains
    )
    return (
        "apiVersion: terramate.io/cli/v1\n"
        "kind: BundleInstance\n"
        "metadata:\n"
        "  name: network_foundation\n"
        f"  uuid: {params.uuid}\n"
        "spec:\n"
        '  source: "/src/bundles/core_infrastructure/network_foundation"\n'
        "environments:\n"
        f"  {params.environment}:\n"
        "    inputs:\n"
        "      business_domains:\n"
        f"{domains_yaml}\n"
    )


render_foundation_config = render_network_foundation_config


def add_business_domain_patch(
    domains: list[str],
    subnet_size: str = "small",
    environment: str = "sbx",
    default_uuid: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that appends business domain(s) under
    environments.{environment}.inputs.business_domains.{domain}.subnet_size without modifying existing domains.
    """

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        doc = copy.deepcopy(document)
        if not doc:
            doc = {
                "apiVersion": "terramate.io/cli/v1",
                "kind": "BundleInstance",
                "metadata": {
                    "name": "network_foundation",
                    "uuid": default_uuid or str(uuid.uuid4()),
                },
                "spec": {
                    "source": QuotedStr("/src/bundles/core_infrastructure/network_foundation"),
                },
                "environments": {
                    environment: {
                        "inputs": {
                            "business_domains": {
                                "controltower": {
                                    "subnet_size": QuotedStr("small"),
                                },
                            },
                        }
                    }
                },
            }

        # Clean up any misplaced spec.environments and merge into top-level environments
        spec = doc.setdefault("spec", {})
        misplaced_envs = spec.pop("environments", None)
        envs = doc.setdefault("environments", {})
        if misplaced_envs and isinstance(misplaced_envs, dict):
            for env_name, env_val in misplaced_envs.items():
                target_env = envs.setdefault(env_name, {})
                if isinstance(env_val, dict):
                    target_inputs = target_env.setdefault("inputs", {})
                    target_bds = target_inputs.setdefault("business_domains", {})
                    src_bds = env_val.get("inputs", {}).get("business_domains", {})
                    if isinstance(src_bds, dict):
                        for k, v in src_bds.items():
                            if k not in target_bds:
                                target_bds[k] = v
                    elif isinstance(src_bds, list):
                        for k in src_bds:
                            if k not in target_bds:
                                target_bds[k] = {"subnet_size": QuotedStr("small")}

        env_config = envs.setdefault(environment, {})
        inputs = env_config.setdefault("inputs", {})
        existing_domains = inputs.setdefault("business_domains", {})

        # If existing_domains was previously a list (legacy transition), convert to dict
        if isinstance(existing_domains, list):
            existing_dict = {
                d: {"subnet_size": QuotedStr("small")} for d in existing_domains
            }
            inputs["business_domains"] = existing_dict
            existing_domains = existing_dict

        for domain in domains:
            if domain not in existing_domains:
                existing_domains[domain] = {"subnet_size": QuotedStr(subnet_size)}
            else:
                if not isinstance(existing_domains[domain], dict):
                    existing_domains[domain] = {"subnet_size": QuotedStr(subnet_size)}
                elif "subnet_size" not in existing_domains[domain]:
                    existing_domains[domain]["subnet_size"] = QuotedStr(subnet_size)

        return doc

    return patch


def business_domain_config_path(environment: str = "sbx", domain: str = "controltower") -> str:
    """The bundle config file for an individual business domain stack."""
    return f"src/configs/{environment}/domain_stacks/business_domain/{domain}/business_domain_{domain}.tm.yml"


def render_business_domain_config(
    domain: str,
    environment: str = "sbx",
    step_uuid: str | None = None,
    storage_configuration_id: str = "",
) -> str:
    """The business domain stack's Terramate bundle instance config file."""
    patch = add_business_domain_stack_patch(
        domain=domain,
        environment=environment,
        default_uuid=step_uuid or "18b50cb1-f2ca-402a-af0f-6c97b8dfde0e",
        storage_configuration_id=storage_configuration_id,
    )
    return dump_yaml(patch({}))


def add_business_domain_stack_patch(
    domain: str,
    environment: str = "sbx",
    default_uuid: str | None = None,
    admin_roles: list[str] | None = None,
    user_roles: list[str] | None = None,
    storage_configuration_id: str = "",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that creates or updates a business domain stack configuration.

    If the document is empty, creates the full BundleInstance.
    If the document exists, appends or updates the requested environment under `environments`.
    """

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        doc = copy.deepcopy(document)
        if not doc:
            doc = {
                "apiVersion": "terramate.io/cli/v1",
                "kind": "BundleInstance",
                "metadata": {
                    "name": f"business_domain_{domain}",
                    "uuid": QuotedStr(default_uuid or str(uuid.uuid4())),
                },
                "spec": {
                    "source": QuotedStr("/src/bundles/domain_stacks/business_domain"),
                },
                "environments": {},
            }

        # Clean up any misplaced spec.environments and merge into top-level environments
        spec = doc.setdefault("spec", {})
        misplaced_envs = spec.pop("environments", None)
        envs = doc.setdefault("environments", {})
        if misplaced_envs and isinstance(misplaced_envs, dict):
            for env_name, env_val in misplaced_envs.items():
                target_env = envs.setdefault(env_name, {})
                if isinstance(env_val, dict):
                    target_inputs = target_env.setdefault("inputs", {})
                    src_inputs = env_val.get("inputs", {})
                    if isinstance(src_inputs, dict):
                        for k, v in src_inputs.items():
                            target_inputs.setdefault(k, v)

        env_config = envs.setdefault(environment, {})
        inputs = env_config.setdefault("inputs", {})

        inputs.setdefault("network_foundation", QuotedStr("network_foundation"))
        inputs.setdefault("domain_name", QuotedStr(domain))
        inputs.setdefault(
            "domain_role_permissions",
            {
                "admin": list(admin_roles) if admin_roles is not None else [],
                "user": list(user_roles) if user_roles is not None else [],
            },
        )
        inputs.setdefault("storage_configuration_id", QuotedStr(storage_configuration_id))

        return doc

    return patch


class WorkspaceRecipe(Recipe):
    type = "workspace"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        domain_edits = [
            EditFile(
                business_domain_config_path(params.environment, d),
                add_business_domain_stack_patch(
                    domain=d,
                    environment=params.environment,
                    default_uuid=params.business_domain_uuid if len(params.business_domains) == 1 else None,
                    admin_roles=params.groups,
                    user_roles=[],
                ),
            )
            for d in params.business_domains
        ]
        return Playbook(
            steps=[
                StepSpec(
                    key="network_foundation",
                    bundle_edits=[
                        EditFile(
                            network_foundation_config_path(params.environment),
                            add_business_domain_patch(
                                domains=params.business_domains,
                                subnet_size=params.subnet_size,
                                environment=params.environment,
                                default_uuid=params.uuid,
                            ),
                        ),
                    ],
                ),
                StepSpec(
                    key="business_domain",
                    depends_on=["network_foundation"],
                    bundle_edits=domain_edits,
                ),
            ]
        )


class FoundationRecipe(Recipe):
    type = "foundation"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        return WorkspaceRecipe().build(params)


class NetworkFoundationRecipe(Recipe):
    type = "network_foundation"
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
