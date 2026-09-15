"""`_resolve_edits` (#22): rebuilds a Step's real `bundle_edits` from its
Recipe and substitutes resolved `${steps.<key>.outputs.<name>}` placeholders
with real values, so the real GitHubClient commits actual content instead of
the placeholder token (architecture.md §15.1). Pure functions, no DB/network.
"""
from __future__ import annotations

from pydantic import BaseModel

from server import orchestrator
from server.models import ProvisioningRequest, Step
from server.recipes.framework import AddFile, EditFile, OutputRef, Playbook, Recipe, StepSpec
from server.recipes.registry import RECIPES
from server.recipes.workspace import bind_workspace_patch, set_owner_patch


class _MockTwoStepParams(BaseModel):
    name: str
    metastore: str
    domain_owner: str
    groups: list[str] = []


class _MockTwoStepRecipe(Recipe):
    type = "_test_two_step"
    params_model = _MockTwoStepParams

    def build(self, params: _MockTwoStepParams) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="create",
                    bundle_edits=[
                        AddFile(f"stacks/workspaces/{params.name}/stack.tm.hcl", f'stack "{params.name}" {{}}'),
                        AddFile(f"stacks/workspaces/{params.name}/inputs.yaml", f"name: {params.name}"),
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
                        EditFile(f"stacks/workspaces/{params.name}/inputs.yaml", set_owner_patch(params.domain_owner)),
                    ],
                ),
            ]
        )


RECIPES["_test_two_step"] = _MockTwoStepRecipe()


def _request(type_: str, params: dict) -> ProvisioningRequest:
    return ProvisioningRequest(
        id="req-1",
        type=type_,
        params=params,
        version="v1",
        requester="tester",
        idempotency_key="idem-1",
        status="in_progress",
    )


def _step(request: ProvisioningRequest, key: str, **kwargs) -> Step:
    step = Step(
        id="step-1",
        request_id=request.id,
        ordinal=0,
        key=key,
        status="queued",
        depends_on=kwargs.pop("depends_on", []),
        produces=kwargs.pop("produces", []),
        consumes=kwargs.pop("consumes", []),
    )
    step.request = request
    return step


def test_resolve_edits_substitutes_workspace_id_into_bind_step_content():
    request = _request(
        "_test_two_step",
        {"name": "analytics", "metastore": "main", "domain_owner": "platform-team", "groups": ["data-eng"]},
    )
    step = _step(
        request,
        "bind",
        consumes=[{"step_key": "create", "output_name": "workspace_id"}],
    )
    resolved = [({"step_key": "create", "output_name": "workspace_id"}, "ws-42")]

    edits = orchestrator._resolve_edits(step, resolved)

    assert len(edits) == 2
    bindings_edit, inputs_edit = edits
    assert isinstance(bindings_edit, EditFile)
    patched = bindings_edit.patch({"bindings": []})
    assert patched["bindings"][-1]["workspace_id"] == "ws-42"
    assert "${steps.create.outputs.workspace_id}" not in str(patched)


def test_resolve_edits_is_a_no_op_substitution_for_a_step_with_no_consumes():
    request = _request(
        "workspace",
        {"name": "analytics"},
    )
    step = _step(request, "foundation")

    edits = orchestrator._resolve_edits(step, resolved=[])

    assert len(edits) == 1
    assert isinstance(edits[0], EditFile)
    assert edits[0].path == "src/configs/sbx/core_infrastructure/foundation/foundation.tm.yml"
    patched = edits[0].patch(
        {"environments": {"sbx": {"inputs": {"business_domains": ["controltower"]}}}}
    )
    assert patched["environments"]["sbx"]["inputs"]["business_domains"] == [
        "controltower",
        "analytics",
    ]


def test_resolve_edits_for_schema_recipe_single_step():
    request = _request(
        "schema", {"catalog": "research", "name": "bronze", "owner": "data-eng", "comment": None}
    )
    step = _step(request, "add-schema")

    edits = orchestrator._resolve_edits(step, resolved=[])

    assert len(edits) == 1
    edit_file = edits[0]
    patched = edit_file.patch({"schemas": []})
    assert patched["schemas"][-1]["name"] == "bronze"


def test_substitute_text_replaces_every_occurrence():
    text = orchestrator._substitute_text(
        "a=${steps.create.outputs.x}, b=${steps.create.outputs.x}",
        {"${steps.create.outputs.x}": "42"},
    )
    assert text == "a=42, b=42"


def test_substitute_structure_recurses_through_dicts_and_lists():
    structure = {"bindings": [{"workspace_id": "${steps.create.outputs.workspace_id}", "groups": ["g1"]}]}
    result = orchestrator._substitute_structure(
        structure, {"${steps.create.outputs.workspace_id}": "ws-99"}
    )
    assert result["bindings"][0]["workspace_id"] == "ws-99"
    assert result["bindings"][0]["groups"] == ["g1"]
