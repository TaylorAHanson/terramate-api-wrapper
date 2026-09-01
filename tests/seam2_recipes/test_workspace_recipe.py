"""The `workspace` Recipe (architecture.md §15.1, #20): a 2-Step Playbook —
`create` produces an apply-derived `workspace_id`; `bind` depends on `create`
and consumes it by reference. Proven against golden files rather than a real
repo (the airgap doesn't dissolve until #2/#15).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from server.recipes.workspace import (
    WorkspaceParams,
    WorkspaceRecipe,
    bind_workspace_patch,
    set_owner_patch,
)
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_workspace_recipe_matches_golden_playbook():
    params = WorkspaceParams(
        name="analytics",
        metastore="main",
        domain_owner="platform-team",
        groups=["data-eng", "platform"],
    )
    playbook = WorkspaceRecipe().build(params)
    assert_matches_golden(playbook, GOLDEN_DIR / "workspace_case.json")


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
