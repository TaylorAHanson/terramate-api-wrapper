"""The `unity_catalog` Recipe: a business domain's Unity Catalog catalogs.

One stack per domain. Its default catalog (`{domain}_{environment}`) is always
created; each entry in `catalog_suffix` adds one more
(`{domain}_{suffix}_{environment}`). One PR that creates (or appends suffixes to)
`src/configs/{environment}/domain_stacks/data_domain/unity_catalog/{domain}/{domain}_unity_catalog.tm.yml`
and registers `{domain}_unity_catalog` in `stack_ids` (a no-op if the stack
already exists).
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes import stack_ids
from server.recipes.bundle_instance import bundle_instance_patch
from server.recipes.framework import EditFile, Playbook, Recipe, StepSpec
from server.recipes.workspace import default_workspace_name


def catalog_name(domain: str, environment: str, suffix: str | None = None) -> str:
    """e.g. `controltower_sbx`, or `controltower_ai_sbx` for suffix `ai`."""
    return f"{domain}_{suffix}_{environment}" if suffix else f"{domain}_{environment}"


class UnityCatalogParams(BaseModel):
    business_domain: str
    environment: str = "sbx"
    workspace_name: str | None = None
    catalog_suffixes: list[str] = Field(default_factory=list)
    stack_uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @model_validator(mode="after")
    def _apply_defaults(self) -> "UnityCatalogParams":
        if not self.workspace_name:
            self.workspace_name = default_workspace_name(self.business_domain, self.environment)
        return self


class UnityCatalogProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "unity_catalog"`."""

    type: Literal["unity_catalog"]
    params: UnityCatalogParams


def unity_catalog_stack_name(domain: str) -> str:
    return f"{domain}_unity_catalog"


def unity_catalog_config_path(environment: str, domain: str) -> str:
    return (
        f"src/configs/{environment}/domain_stacks/data_domain/unity_catalog/{domain}/"
        f"{unity_catalog_stack_name(domain)}.tm.yml"
    )


def unity_catalog_patch(params: UnityCatalogParams) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return bundle_instance_patch(
        name=unity_catalog_stack_name(params.business_domain),
        stack_uuid=params.stack_uuid,
        source="/src/bundles/domain_stacks/data_domain/unity_catalog",
        environment=params.environment,
        inputs={"domain_name": params.business_domain, "workspace_name": params.workspace_name},
        append_to={"catalog_suffix": params.catalog_suffixes},
    )


class UnityCatalogRecipe(Recipe):
    type = "unity_catalog"
    params_model = UnityCatalogParams
    parallel = False

    def build(self, params: UnityCatalogParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="unity_catalog",
                    bundle_edits=[
                        EditFile(
                            unity_catalog_config_path(params.environment, params.business_domain),
                            unity_catalog_patch(params),
                        ),
                        stack_ids.register_stack_id(
                            params.environment,
                            stack_ids.UNITY_CATALOG,
                            unity_catalog_stack_name(params.business_domain),
                            params.stack_uuid,
                        ),
                    ],
                ),
            ]
        )
