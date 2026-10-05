"""The `workspace_folder` Recipe: a business domain's workspace folders.

One PR that creates (or appends folders to)
`src/configs/{environment}/domain_stacks/business_domain/workspace_folder/{domain}/{domain}_workspace_folder.tm.yml`
and registers `{domain}_workspace_folder` in `stack_ids`.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from server.recipes import stack_ids
from server.recipes.bundle_instance import bundle_instance_patch
from server.recipes.framework import EditFile, Playbook, Recipe, StepSpec
from server.recipes.workspace import default_workspace_name


def default_folder_names(domain: str) -> list[str]:
    return [
        f"{domain}_assets/ai_apps",
        f"{domain}_assets/ai_ml",
        f"{domain}_assets/data_aibi",
        f"{domain}_assets/data_apps",
        f"{domain}_assets/data_deng",
        f"{domain}_aibi_genie",
        f"{domain}_aibi_dashboards",
    ]


class WorkspaceFolderParams(BaseModel):
    business_domain: str
    environment: str = "sbx"
    workspace_name: str | None = None
    folder_names: list[str] | None = None
    stack_uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @model_validator(mode="after")
    def _apply_defaults(self) -> "WorkspaceFolderParams":
        if not self.workspace_name:
            self.workspace_name = default_workspace_name(self.business_domain, self.environment)
        if self.folder_names is None:
            self.folder_names = default_folder_names(self.business_domain)
        return self


class WorkspaceFolderProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "workspace_folder"`."""

    type: Literal["workspace_folder"]
    params: WorkspaceFolderParams


def workspace_folder_stack_name(domain: str) -> str:
    return f"{domain}_workspace_folder"


def workspace_folder_config_path(environment: str, domain: str) -> str:
    return (
        f"src/configs/{environment}/domain_stacks/business_domain/workspace_folder/{domain}/"
        f"{workspace_folder_stack_name(domain)}.tm.yml"
    )


def workspace_folder_patch(params: WorkspaceFolderParams) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return bundle_instance_patch(
        name=workspace_folder_stack_name(params.business_domain),
        stack_uuid=params.stack_uuid,
        source="/src/bundles/domain_stacks/business_domain/workspace_folder",
        environment=params.environment,
        inputs={"domain_name": params.business_domain, "workspace_name": params.workspace_name},
        append_to={"folder_names": params.folder_names},
    )


class WorkspaceFolderRecipe(Recipe):
    type = "workspace_folder"
    params_model = WorkspaceFolderParams

    def build(self, params: WorkspaceFolderParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="workspace_folder",
                    bundle_edits=[
                        EditFile(
                            workspace_folder_config_path(params.environment, params.business_domain),
                            workspace_folder_patch(params),
                        ),
                        stack_ids.register_stack_id(
                            params.environment,
                            stack_ids.WORKSPACE_FOLDER,
                            workspace_folder_stack_name(params.business_domain),
                            params.stack_uuid,
                        ),
                    ],
                ),
            ]
        )
