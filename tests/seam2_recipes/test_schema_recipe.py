"""The `schema` Recipe (architecture.md §15.1, #18): single-Step, "find the
right catalog, then add the schema to it" — proven against golden files
rather than a real repo (the airgap doesn't dissolve until #2/#15).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from server.recipes.schema import SchemaParams, SchemaRecipe, add_schema_patch
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_schema_recipe_matches_golden_playbook():
    params = SchemaParams(catalog="research", name="bronze", owner="data-eng", comment="raw landing data")
    playbook = SchemaRecipe().build(params)
    assert_matches_golden(playbook, GOLDEN_DIR / "schema_case.json")


def test_add_schema_patch_produces_the_expected_file_diff():
    before = yaml.safe_load((GOLDEN_DIR / "schema_catalog_before.yaml").read_text())

    patch = add_schema_patch(name="bronze", owner="data-eng", comment="raw landing data")
    after = patch(before)

    expected = yaml.safe_load((GOLDEN_DIR / "schema_catalog_after.yaml").read_text())
    assert after == expected
