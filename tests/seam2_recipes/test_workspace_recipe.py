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
    add_business_domain_stack_patch,
    bind_workspace_patch,
    business_domain_config_path,
    network_foundation_config_path,
    render_business_domain_config,
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
    assert len(playbook.steps) == 2

    # Step 1: network_foundation
    step1 = playbook.steps[0]
    assert step1.key == "network_foundation"
    assert step1.produces == ()
    assert step1.consumes == ()
    assert step1.depends_on == ()
    assert len(step1.bundle_edits) == 1
    edit1 = step1.bundle_edits[0]
    assert isinstance(edit1, EditFile)
    assert edit1.path == "src/configs/sbx/core_infrastructure/network_foundation/network_foundation.tm.yml"

    # Test patch execution for step 1
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())
    after = edit1.patch(before)
    assert after["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "finance": {"subnet_size": "small"},
    }
    assert "environments" not in after.get("spec", {})

    # Step 2: business_domain
    step2 = playbook.steps[1]
    assert step2.key == "business_domain"
    assert step2.produces == ()
    assert step2.consumes == ()
    assert step2.depends_on == ["network_foundation"]
    assert len(step2.bundle_edits) == 1
    edit2 = step2.bundle_edits[0]
    assert isinstance(edit2, EditFile)
    assert edit2.path == "src/configs/sbx/domain_stacks/business_domain/finance/business_domain_finance.tm.yml"

    # Step 2 creates the YAML when empty
    doc2 = edit2.patch({})
    assert doc2["apiVersion"] == "terramate.io/cli/v1"
    assert doc2["metadata"]["name"] == "business_domain_finance"
    assert doc2["spec"]["source"] == "/src/bundles/domain_stacks/business_domain"
    assert doc2["environments"]["sbx"]["inputs"]["domain_name"] == "finance"
    assert doc2["environments"]["sbx"]["inputs"]["network_foundation"] == "network_foundation"


def test_add_business_domain_stack_patch_creates_yaml_when_empty():
    patch = add_business_domain_stack_patch(
        domain="controltower",
        environment="sbx",
        default_uuid="18b50cb1-f2ca-402a-af0f-6c97b8dfde0e",
    )
    result = patch({})
    expected = yaml.safe_load((GOLDEN_DIR / "business_domain_created.yaml").read_text())
    assert result == expected


def test_add_business_domain_stack_patch_edits_yaml_when_adding_env():
    before = yaml.safe_load((GOLDEN_DIR / "business_domain_created.yaml").read_text())
    patch = add_business_domain_stack_patch(
        domain="controltower",
        environment="dev",
        default_uuid="18b50cb1-f2ca-402a-af0f-6c97b8dfde0e",
    )
    after = patch(before)
    expected = yaml.safe_load((GOLDEN_DIR / "business_domain_env_added.yaml").read_text())
    assert after == expected


def test_add_business_domain_patch_produces_the_expected_file_diff():
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())

    patch = add_business_domain_patch(domains=["finance"], subnet_size="small", environment="sbx")
    after = patch(before)

    expected = yaml.safe_load((GOLDEN_DIR / "foundation_after.yaml").read_text())
    assert after == expected


def test_dump_yaml_diff_only_adds_new_domain_with_exact_indentation_and_quotes():
    import difflib
    from server.yaml_util import dump_yaml, load_yaml

    before_text = (GOLDEN_DIR / "foundation_before.yaml").read_text()
    doc = load_yaml(before_text)
    patch = add_business_domain_patch(domains=["finance"], subnet_size="small", environment="sbx")
    patched = patch(doc)
    rendered = dump_yaml(patched)

    diff = list(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
        )
    )
    removed_lines = [line for line in diff if line.startswith("-") and not line.startswith("---")]
    added_lines = [line for line in diff if line.startswith("+") and not line.startswith("+++")]

    assert removed_lines == [], f"Unexpected removed/changed lines: {removed_lines}"
    assert added_lines == [
        "+        finance:\n",
        '+          subnet_size: "small"\n',
    ], f"Unexpected added lines: {added_lines}"


def test_add_business_domain_patch_is_idempotent():
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())

    patch = add_business_domain_patch(domains=["controltower"], environment="sbx")
    after = patch(before)

    # controltower was already in before, so it must not be duplicated
    assert after["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"}
    }


def test_add_business_domain_patch_custom_subnet_size():
    before = yaml.safe_load((GOLDEN_DIR / "foundation_before.yaml").read_text())

    patch = add_business_domain_patch(domains=["analytics"], subnet_size="medium", environment="sbx")
    after = patch(before)

    assert after["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "analytics": {"subnet_size": "medium"},
    }


def test_add_business_domain_patch_heals_misplaced_spec_environments():
    # If a document previously had environments under spec, patch cleans it up
    doc_with_duplicate = {
        "apiVersion": "terramate.io/cli/v1",
        "kind": "BundleInstance",
        "metadata": {"name": "network_foundation", "uuid": "ef845e8f-8f75-474b-9133-95d1aa65e6b9"},
        "spec": {
            "source": "/src/bundles/core_infrastructure/network_foundation",
            "environments": {
                "sbx": {
                    "inputs": {"business_domains": {"finance": {"subnet_size": "large"}}}
                }
            },
        },
        "environments": {
            "sbx": {
                "inputs": {"business_domains": {"controltower": {"subnet_size": "small"}}}
            }
        },
    }

    patch = add_business_domain_patch(domains=["finance"], subnet_size="large", environment="sbx")
    healed = patch(doc_with_duplicate)

    assert "environments" not in healed["spec"]
    assert healed["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "finance": {"subnet_size": "large"},
    }


def test_add_business_domain_patch_initializes_empty_document():
    patch = add_business_domain_patch(
        domains=["finance"], subnet_size="small", environment="sbx", default_uuid="custom-uuid"
    )
    result = patch({})

    assert result["apiVersion"] == "terramate.io/cli/v1"
    assert result["metadata"]["uuid"] == "custom-uuid"
    assert "environments" not in result["spec"]
    assert "sbx" in result["environments"]
    assert result["environments"]["sbx"]["inputs"]["business_domains"] == {
        "controltower": {"subnet_size": "small"},
        "finance": {"subnet_size": "small"},
    }


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
