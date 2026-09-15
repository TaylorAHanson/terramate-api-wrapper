"""The `workspace` Recipe: customer Foundation stack step ("first step spec for the
workspace").

Proven against golden files (Seam 2).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from server.recipes.framework import EditFile
from server.recipes.workspace import (
    FoundationRecipe,
    WorkspaceParams,
    WorkspaceRecipe,
    add_business_domain_patch,
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


def test_workspace_recipe_uses_sbx_environment_and_edits_foundation():
    params = WorkspaceParams(name="sbx-test", business_domain="finance")
    playbook = WorkspaceRecipe().build(params)
    assert len(playbook.steps) == 1
    step = playbook.steps[0]
    assert step.key == "foundation"
    assert step.produces == ()
    assert step.consumes == ()
    assert step.depends_on == ()
    assert len(step.bundle_edits) == 1
    edit = step.bundle_edits[0]
    assert isinstance(edit, EditFile)
    assert edit.path == "src/configs/sbx/core_infrastructure/foundation/foundation.tm.yml"

    # Test patch execution
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())
    after = edit.patch(before)
    assert after["environments"]["sbx"]["inputs"]["business_domains"] == [
        "controltower",
        "finance",
    ]
    assert "environments" not in after.get("spec", {})


def test_add_business_domain_patch_produces_the_expected_file_diff():
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())

    patch = add_business_domain_patch(domains=["finance"], environment="sbx")
    after = patch(before)

    expected = yaml.safe_load((GOLDEN_DIR / "foundation_after.yaml").read_text())
    assert after == expected


def test_add_business_domain_patch_is_idempotent():
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())

    patch = add_business_domain_patch(domains=["controltower"], environment="sbx")
    after = patch(before)

    # controltower was already in before, so it must not be duplicated
    assert after["environments"]["sbx"]["inputs"]["business_domains"] == ["controltower"]


def test_add_business_domain_patch_heals_misplaced_spec_environments():
    # If a document previously had environments under spec, patch cleans it up
    doc_with_duplicate = {
        "apiVersion": "terramate.io/cli/v1",
        "kind": "BundleInstance",
        "metadata": {"name": "foundation", "uuid": "ef845e8f-8f75-474b-9133-95d1aa65e6b9"},
        "spec": {
            "source": "/src/bundles/core_infrastructure/foundation",
            "environments": {
                "sbx": {
                    "inputs": {"business_domains": ["finance"]}
                }
            },
        },
        "environments": {
            "sbx": {
                "inputs": {"business_domains": ["controltower"]}
            }
        },
    }

    patch = add_business_domain_patch(domains=["finance"], environment="sbx")
    healed = patch(doc_with_duplicate)

    assert "environments" not in healed["spec"]
    assert healed["environments"]["sbx"]["inputs"]["business_domains"] == [
        "controltower",
        "finance",
    ]


def test_add_business_domain_patch_initializes_empty_document():
    patch = add_business_domain_patch(
        domains=["finance"], environment="sbx", default_uuid="custom-uuid"
    )
    result = patch({})

    assert result["apiVersion"] == "terramate.io/cli/v1"
    assert result["metadata"]["uuid"] == "custom-uuid"
    assert "environments" not in result["spec"]
    assert "sbx" in result["environments"]
    assert result["environments"]["sbx"]["inputs"]["business_domains"] == [
        "controltower",
        "finance",
    ]


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
