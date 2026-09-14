"""Seam 1 fixture setup.

Restores the 2-step create→bind Playbook under `RECIPES["workspace"]` during
Seam 1 tests so the request-lifecycle, row-locking, and step-ordering tests
(which specifically verify 2-step value passing and outputs) continue to prove
the orchestrator engine end to end.
"""
from __future__ import annotations

import pytest

from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec
from server.recipes.registry import RECIPES
from server.recipes.workspace import (
    WorkspaceParams,
    bind_workspace_patch,
    set_owner_patch,
)


class _Seam1WorkspaceRecipe(Recipe):
    type = "workspace"
    params_model = WorkspaceParams

    def build(self, params: WorkspaceParams) -> Playbook:
        inputs_path = f"stacks/workspaces/{params.name}/inputs.yaml"
        return Playbook(
            steps=[
                StepSpec(
                    key="create",
                    bundle_edits=[
                        AddFile(
                            f"stacks/workspaces/{params.name}/stack.tm.hcl",
                            f'stack "{params.name}" {{\n  source = "modules/workspace"\n}}\n',
                        ),
                        AddFile(inputs_path, f"name: {params.name}\nmetastore: {params.metastore or ''}\n"),
                    ],
                    produces=["workspace_id"],
                ),
                StepSpec(
                    key="bind",
                    depends_on=["create"],
                    consumes=[OutputRef("create", "workspace_id")],
                    bundle_edits=[
                        EditFile(
                            f"stacks/metastores/{params.metastore}/bindings.tm.yaml",
                            bind_workspace_patch("${steps.create.outputs.workspace_id}", params.groups),
                        ),
                        EditFile(inputs_path, set_owner_patch(params.domain_owner or "")),
                    ],
                ),
            ]
        )


@pytest.fixture(autouse=True)
def _use_seam1_workspace_fixture():
    orig = RECIPES.get("workspace")
    RECIPES["workspace"] = _Seam1WorkspaceRecipe()
    yield
    if orig:
        RECIPES["workspace"] = orig
