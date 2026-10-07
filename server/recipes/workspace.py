"""The `workspace` Recipe: a business domain's network foundation entry and its
workspace stack, as two PRs.

1. `network_foundation` — edits
   `src/configs/{environment}/core_infrastructure/network_foundation/network_foundation.tm.yml`
   to append the requested business domain(s) with subnet_size under
   `environments.{environment}.inputs.business_domains`. The network foundation
   stack is one per environment and already has its `stack_ids` entry.
2. `business_domain` — creates (or adds the environment to)
   `src/configs/{environment}/domain_stacks/business_domain/workspace/{domain}/{domain}_workspace.tm.yml`
   per domain, and registers `{domain}_workspace` in `stack_ids`.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes import stack_ids
from server.recipes.bundle_instance import bundle_instance_patch
from server.recipes.framework import EditFile, Playbook, Recipe, StepSpec
from server.yaml_util import QuotedStr


class WorkspaceParams(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = ""
    environment: str = "sbx"
    business_domain: str = "controltower"
    business_domains: list[str] = Field(default_factory=lambda: ["controltower"])
    subnet_size: Literal["small", "medium", "large"] | str = "small"
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    business_domain_uuid: str | None = None
    # One workspace-stack uuid per domain, minted at request time and persisted
    # with `params`, so the stack's `metadata.uuid` and its `stack_ids` entry
    # agree and stay stable when the Playbook is rebuilt at claim time.
    stack_uuids: dict[str, str] = Field(default_factory=dict)
    # Optional fields for backwards compatibility with earlier prototypes
    metastore: str | None = None
    domain_owner: str | None = None
    groups: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mint_stack_uuids(self) -> "WorkspaceParams":
        if self.business_domain_uuid and len(self.business_domains) == 1:
            self.stack_uuids.setdefault(self.business_domains[0], self.business_domain_uuid)
        for domain in self.business_domains:
            self.stack_uuids.setdefault(domain, str(uuid.uuid4()))
        return self

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


def workspace_stack_name(domain: str) -> str:
    return f"{domain}_workspace"


def default_workspace_name(domain: str, environment: str) -> str:
    """The Databricks workspace name the domain stacks reference, e.g. `controltower_ws_sbx`."""
    return f"{domain}_ws_{environment}"


def workspace_config_path(environment: str, domain: str) -> str:
    """The bundle config file for a business domain's workspace stack."""
    return (
        f"src/configs/{environment}/domain_stacks/business_domain/workspace/{domain}/"
        f"{workspace_stack_name(domain)}.tm.yml"
    )


def workspace_stack_patch(
    domain: str,
    environment: str,
    stack_uuid: str,
    storage_configuration_id: str = "",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Creates the workspace stack file, or adds `environment` to an existing one."""
    return bundle_instance_patch(
        name=workspace_stack_name(domain),
        stack_uuid=stack_uuid,
        source="/src/bundles/domain_stacks/business_domain/workspace",
        environment=environment,
        inputs={
            "network_foundation": "network_foundation",
            "domain_name": domain,
            "storage_configuration_id": storage_configuration_id,
        },
    )


class WorkspaceRecipe(Recipe):
    type = "workspace"
    params_model = WorkspaceParams
    parallel = False

    def build(self, params: WorkspaceParams) -> Playbook:
        workspace_edits = []
        for domain in params.business_domains:
            stack_uuid = params.stack_uuids[domain]
            workspace_edits += [
                EditFile(
                    workspace_config_path(params.environment, domain),
                    workspace_stack_patch(domain, params.environment, stack_uuid),
                ),
                stack_ids.register_stack_id(
                    params.environment, stack_ids.WORKSPACE, workspace_stack_name(domain), stack_uuid
                ),
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
                    bundle_edits=workspace_edits,
                ),
            ]
        )


class FoundationRecipe(Recipe):
    type = "foundation"
    params_model = WorkspaceParams
    parallel = False

    def build(self, params: WorkspaceParams) -> Playbook:
        return WorkspaceRecipe().build(params)


class NetworkFoundationRecipe(Recipe):
    type = "network_foundation"
    params_model = WorkspaceParams
    parallel = False

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
