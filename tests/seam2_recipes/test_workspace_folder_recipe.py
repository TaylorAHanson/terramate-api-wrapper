"""The `workspace_folder` Recipe, proven against golden files (Seam 2)."""
from __future__ import annotations

from pathlib import Path

from server.recipes.framework import EditFile, EditText
from server.recipes.workspace_folder import WorkspaceFolderParams, WorkspaceFolderRecipe, workspace_folder_patch
from server.yaml_util import dump_yaml, load_yaml
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"
_UUID = "7d983863-4bbc-412e-8039-dd145266ae82"


def test_workspace_folder_recipe_matches_golden_playbook():
    params = WorkspaceFolderParams(business_domain="controltower", stack_uuid=_UUID)
    assert_matches_golden(WorkspaceFolderRecipe().build(params), GOLDEN_DIR / "workspace_folder_case.json")


def test_defaults_render_the_repo_file_exactly():
    params = WorkspaceFolderParams(business_domain="controltower", stack_uuid=_UUID)

    assert dump_yaml(workspace_folder_patch(params)({})) == (GOLDEN_DIR / "workspace_folder_created.yaml").read_text()


def test_extra_folders_are_appended_to_an_existing_file_without_duplicates():
    before = load_yaml((GOLDEN_DIR / "workspace_folder_created.yaml").read_text())
    params = WorkspaceFolderParams(
        business_domain="controltower",
        folder_names=["controltower_assets/ai_apps", "controltower_assets/data_gov"],
    )

    after = workspace_folder_patch(params)(before)

    folders = after["environments"]["sbx"]["inputs"]["folder_names"]
    assert folders[-1] == "controltower_assets/data_gov"
    assert folders.count("controltower_assets/ai_apps") == 1
    assert after["metadata"]["uuid"] == _UUID


def test_the_step_registers_the_stack_id_with_the_same_uuid():
    params = WorkspaceFolderParams(business_domain="finance", environment="dev")
    step = WorkspaceFolderRecipe().build(params).steps[0]

    stack_edit, id_edit = step.bundle_edits
    assert isinstance(stack_edit, EditFile)
    assert stack_edit.path == "src/configs/dev/domain_stacks/business_domain/workspace_folder/finance/finance_workspace_folder.tm.yml"
    doc = stack_edit.patch({})
    assert doc["environments"]["dev"]["inputs"]["workspace_name"] == "finance_ws_dev"
    assert doc["environments"]["dev"]["inputs"]["folder_names"][0] == "finance_assets/ai_apps"

    assert isinstance(id_edit, EditText)
    assert id_edit.path == "src/configs/dev/dev_config.tm.hcl"
    config = id_edit.patch((GOLDEN_DIR / "sbx_config_before.tm.hcl").read_text())
    assert f'    finance_workspace_folder      = "{doc["metadata"]["uuid"]}"' in config.splitlines()
