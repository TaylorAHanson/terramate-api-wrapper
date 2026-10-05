"""The `unity_catalog_schema` Recipe, proven against golden files (Seam 2)."""
from __future__ import annotations

from pathlib import Path

from server.recipes.framework import EditFile, EditText
from server.recipes.unity_catalog_schema import (
    UnityCatalogSchemaParams,
    UnityCatalogSchemaRecipe,
    unity_catalog_schema_patch,
)
from server.yaml_util import dump_yaml
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"
_UUID = "328b40c1-7e98-468c-8de2-91e60728fcb2"
_SCHEMAS = ["app_lighthouse_sync", "app_lighthouse_txn", "app_lighthouse_logs"]


def test_unity_catalog_schema_recipe_matches_golden_playbook():
    params = UnityCatalogSchemaParams(business_domain="controltower", schemas=_SCHEMAS, stack_uuid=_UUID)
    assert_matches_golden(UnityCatalogSchemaRecipe().build(params), GOLDEN_DIR / "unity_catalog_schema_case.json")


def test_the_default_catalog_renders_the_repo_file_exactly():
    params = UnityCatalogSchemaParams(business_domain="controltower", schemas=_SCHEMAS, stack_uuid=_UUID)

    assert dump_yaml(unity_catalog_schema_patch(params)({})) == (
        GOLDEN_DIR / "unity_catalog_schema_created.yaml"
    ).read_text()


def test_the_catalog_can_be_named_by_suffix_or_in_full():
    by_suffix = UnityCatalogSchemaParams(business_domain="controltower", catalog_suffix="ai")
    by_name = UnityCatalogSchemaParams(business_domain="controltower", catalog_name="controltower_ai_sbx")

    assert by_suffix.catalog_name == by_name.catalog_name == "controltower_ai_sbx"


def test_schemas_are_appended_to_an_existing_empty_catalog_file():
    before = {
        "apiVersion": "terramate.io/cli/v1",
        "kind": "BundleInstance",
        "metadata": {"name": "controltower_ai_sbx_unity_catalog_schema", "uuid": "69cfc50e"},
        "spec": {"source": "/src/bundles/domain_stacks/data_domain/unity_catalog_schema"},
        "environments": {
            "sbx": {
                "inputs": {
                    "domain_name": "controltower",
                    "workspace_name": "controltower_ws_sbx",
                    "catalog_name": "controltower_ai_sbx",
                    "schema_list": [],
                }
            }
        },
    }
    params = UnityCatalogSchemaParams(business_domain="controltower", catalog_suffix="ai", schemas=["bronze"])

    after = unity_catalog_schema_patch(params)(before)

    assert after["environments"]["sbx"]["inputs"]["schema_list"] == ["bronze"]
    assert after["metadata"]["uuid"] == "69cfc50e"


def test_the_step_targets_the_catalogs_file_and_registers_its_stack_id():
    params = UnityCatalogSchemaParams(business_domain="controltower", catalog_suffix="ml", schemas=["gold"])
    stack_edit, id_edit = UnityCatalogSchemaRecipe().build(params).steps[0].bundle_edits

    assert isinstance(stack_edit, EditFile)
    assert stack_edit.path == (
        "src/configs/sbx/domain_stacks/data_domain/unity_catalog_schema/controltower/"
        "controltower_ml_sbx_unity_catalog_schema.tm.yml"
    )
    assert isinstance(id_edit, EditText)
    config = id_edit.patch((GOLDEN_DIR / "sbx_config_before.tm.hcl").read_text())
    assert f'    controltower_ml_sbx_unity_catalog_schema = "{params.stack_uuid}"' in config.splitlines()
