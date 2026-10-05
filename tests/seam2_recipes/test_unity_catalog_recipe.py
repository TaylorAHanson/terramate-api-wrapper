"""The `unity_catalog` Recipe, proven against golden files (Seam 2)."""
from __future__ import annotations

import difflib
from pathlib import Path

from server.recipes.framework import EditFile, EditText
from server.recipes.unity_catalog import UnityCatalogParams, UnityCatalogRecipe, catalog_name, unity_catalog_patch
from server.yaml_util import dump_yaml, load_yaml
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"
_UUID = "9c3549fc-60a2-4ea8-bc67-12d6cfa854c9"


def test_unity_catalog_recipe_matches_golden_playbook():
    params = UnityCatalogParams(business_domain="controltower", stack_uuid=_UUID)
    assert_matches_golden(UnityCatalogRecipe().build(params), GOLDEN_DIR / "unity_catalog_case.json")


def test_catalog_names_follow_the_default_and_suffix_convention():
    assert catalog_name("controltower", "sbx") == "controltower_sbx"
    assert catalog_name("controltower", "sbx", "ai") == "controltower_ai_sbx"


def test_a_new_domain_gets_only_its_default_catalog():
    params = UnityCatalogParams(business_domain="controltower", stack_uuid=_UUID)

    assert dump_yaml(unity_catalog_patch(params)({})) == (GOLDEN_DIR / "unity_catalog_created.yaml").read_text()


def test_suffixes_are_appended_to_an_existing_file():
    before_text = (GOLDEN_DIR / "unity_catalog_created.yaml").read_text()
    params = UnityCatalogParams(business_domain="controltower", catalog_suffixes=["ai", "ml"])

    after_text = dump_yaml(unity_catalog_patch(params)(load_yaml(before_text)))

    diff = [
        line
        for line in difflib.unified_diff(before_text.splitlines(), after_text.splitlines(), lineterm="")
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    assert diff == [
        "-      catalog_suffix: []",
        "+      catalog_suffix:",
        '+        - "ai"',
        '+        - "ml"',
    ]


def test_the_step_registers_the_stack_id_and_is_a_no_op_when_already_registered():
    params = UnityCatalogParams(business_domain="controltower")
    stack_edit, id_edit = UnityCatalogRecipe().build(params).steps[0].bundle_edits

    assert isinstance(stack_edit, EditFile)
    assert stack_edit.path == "src/configs/sbx/domain_stacks/data_domain/unity_catalog/controltower/controltower_unity_catalog.tm.yml"
    assert isinstance(id_edit, EditText)
    config = (GOLDEN_DIR / "sbx_config_before.tm.hcl").read_text()
    assert id_edit.patch(config) == config
