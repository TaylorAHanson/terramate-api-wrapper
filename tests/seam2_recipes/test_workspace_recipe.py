"""The `workspace` Recipe: customer Foundation stack step ("first step spec for the
workspace").

Proven against golden files (Seam 2).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from server.recipes.workspace import (
    FoundationRecipe,
    WorkspaceParams,
    WorkspaceRecipe,
    bind_workspace_patch,
    set_owner_patch,
)
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_workspace_recipe_matches_golden_playbook():
    params = WorkspaceParams(
        name="sbx-test",
        uuid="ef845e8f-8f75-474b-9133-95d1aa65e6b9",
        business_domains=["controltower"],
    )
    playbook = WorkspaceRecipe().build(params)
    assert_matches_golden(playbook, GOLDEN_DIR / "workspace_case.json")


def test_foundation_recipe_matches_workspace_playbook():
    params = WorkspaceParams(
        name="sbx-test",
        uuid="ef845e8f-8f75-474b-9133-95d1aa65e6b9",
        business_domains=["controltower"],
    )
    playbook = FoundationRecipe().build(params)
    assert_matches_golden(playbook, GOLDEN_DIR / "workspace_case.json")


def test_workspace_recipe_generates_uuid_and_replaces_environment_key():
    params = WorkspaceParams(name="sbx-test")
    playbook = WorkspaceRecipe().build(params)
    assert len(playbook.steps) == 1
    step = playbook.steps[0]
    assert step.key == "foundation"
    assert step.produces == ()
    assert step.consumes == ()
    assert step.depends_on == ()
    assert len(step.bundle_edits) == 1
    edit = step.bundle_edits[0]
    assert edit.path == "src/configs/sbx-test/core_infrastructure/foundation/foundation.tm.yml"
    assert "sbx-test:" in edit.content
    assert '"controltower"' in edit.content
    assert f"uuid: {params.uuid}" in edit.content


def test_workspace_recipe_custom_business_domain():
    params = WorkspaceParams(name="sbx-test", business_domain="risk-analytics")
    playbook = WorkspaceRecipe().build(params)
    edit = playbook.steps[0].bundle_edits[0]
    assert '"risk-analytics"' in edit.content
    assert '"controltower"' not in edit.content


def test_workspace_recipe_multiple_business_domains():
    params = WorkspaceParams(name="sbx-test", business_domains=["domain-a", "domain-b"])
    playbook = WorkspaceRecipe().build(params)
    edit = playbook.steps[0].bundle_edits[0]
    assert '"domain-a"' in edit.content
    assert '"domain-b"' in edit.content
    assert '"controltower"' not in edit.content


def test_bind_workspace_patch_produces_the_expected_file_diff():
    before = yaml.safe_load((GOLDEN_DIR / "workspace_bindings_before.yaml").read_text())

    patch = bind_workspace_patch(
        "${steps.create.outputs.workspace_id}", groups=["data-eng", "platform"]
    )
    after = patch(before)

    expected = yaml.safe_load((GOLDEN_DIR / "workspace_bindings_after.yaml").read_text())
    assert after == expected


def test_set_owner_patch_produces_the_expected_file_diff():
    before = yaml.safe_load((GOLDEN_DIR / "workspace_inputs_before.yaml").read_text())

    patch = set_owner_patch("platform-team")
    after = patch(before)

    expected = yaml.safe_load((GOLDEN_DIR / "workspace_inputs_after.yaml").read_text())
    assert after == expected
