"""The `unity_catalog_schema` Recipe: schemas in one Unity Catalog catalog.

One stack per catalog. One PR that creates (or appends schemas to)
`src/configs/{environment}/domain_stacks/data_domain/unity_catalog_schema/{domain}/{catalog}_unity_catalog_schema.tm.yml`
and registers `{catalog}_unity_catalog_schema` in `stack_ids` (a no-op if the
stack already exists).
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes import stack_ids
from server.recipes.bundle_instance import bundle_instance_patch
from server.recipes.framework import EditFile, Playbook, Recipe, StepSpec
from server.recipes.unity_catalog import catalog_name
from server.recipes.workspace import default_workspace_name


class UnityCatalogSchemaParams(BaseModel):
    business_domain: str
    environment: str = "sbx"
    workspace_name: str | None = None
    # Either the full catalog name, or the suffix it was created with in the
    # `unity_catalog` stack (omit both for the domain's default catalog).
    catalog_name: str | None = None
    catalog_suffix: str | None = None
    schemas: list[str] = Field(default_factory=list)
    stack_uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @model_validator(mode="after")
    def _apply_defaults(self) -> "UnityCatalogSchemaParams":
        if not self.workspace_name:
            self.workspace_name = default_workspace_name(self.business_domain, self.environment)
        if not self.catalog_name:
            self.catalog_name = catalog_name(self.business_domain, self.environment, self.catalog_suffix)
        return self


class UnityCatalogSchemaProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "unity_catalog_schema"`."""

    type: Literal["unity_catalog_schema"]
    params: UnityCatalogSchemaParams


def unity_catalog_schema_stack_name(catalog: str) -> str:
    return f"{catalog}_unity_catalog_schema"


def unity_catalog_schema_config_path(environment: str, domain: str, catalog: str) -> str:
    return (
        f"src/configs/{environment}/domain_stacks/data_domain/unity_catalog_schema/{domain}/"
        f"{unity_catalog_schema_stack_name(catalog)}.tm.yml"
    )


def unity_catalog_schema_patch(params: UnityCatalogSchemaParams) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return bundle_instance_patch(
        name=unity_catalog_schema_stack_name(params.catalog_name),
        stack_uuid=params.stack_uuid,
        source="/src/bundles/domain_stacks/data_domain/unity_catalog_schema",
        environment=params.environment,
        inputs={
            "domain_name": params.business_domain,
            "workspace_name": params.workspace_name,
            "catalog_name": params.catalog_name,
        },
        append_to={"schema_list": params.schemas},
    )


class UnityCatalogSchemaRecipe(Recipe):
    type = "unity_catalog_schema"
    params_model = UnityCatalogSchemaParams
    parallel = False

    def build(self, params: UnityCatalogSchemaParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="unity_catalog_schema",
                    bundle_edits=[
                        EditFile(
                            unity_catalog_schema_config_path(
                                params.environment, params.business_domain, params.catalog_name
                            ),
                            unity_catalog_schema_patch(params),
                        ),
                        stack_ids.register_stack_id(
                            params.environment,
                            stack_ids.UNITY_CATALOG_SCHEMA,
                            unity_catalog_schema_stack_name(params.catalog_name),
                            params.stack_uuid,
                        ),
                    ],
                ),
            ]
        )
