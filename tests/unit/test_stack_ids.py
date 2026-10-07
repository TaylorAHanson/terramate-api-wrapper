"""`register_stack_id_patch` (ADR-0005): adds a new stack's `key = "<uuid>"`
to the `stack_ids` block of `<env>_config.tm.hcl`, touching only the lines it
adds or re-aligns. Pure functions, no DB/network.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from server.recipes import stack_ids

BEFORE = (Path(__file__).parent.parent / "seam2_recipes" / "golden" / "sbx_config_before.tm.hcl").read_text()


def _changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=""))
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    return removed, added


def test_env_config_path_is_per_environment():
    assert stack_ids.env_config_path("sbx") == "src/configs/sbx/sbx_config.tm.hcl"


def test_appends_to_its_group_aligned_with_the_existing_entries():
    patch = stack_ids.register_stack_id_patch(stack_ids.WORKSPACE, "finance_workspace", "u-1")

    after = patch(BEFORE)

    removed, added = _changed_lines(BEFORE, after)
    assert removed == []
    assert added == ['    finance_workspace      = "u-1"']
    lines = after.splitlines()
    assert lines[lines.index('    controltower_workspace = "3f23efb8-da83-478a-bdb3-23d6b78b38a3"') + 1] == (
        '    finance_workspace      = "u-1"'
    )


def test_a_longer_key_realigns_only_its_own_group():
    patch = stack_ids.register_stack_id_patch(
        stack_ids.UNITY_CATALOG_SCHEMA, "wealth_management_sbx_unity_catalog_schema", "u-2"
    )

    after = patch(BEFORE)

    removed, added = _changed_lines(BEFORE, after)
    assert removed == [
        '    controltower_sbx_unity_catalog_schema    = "40f02f32-e60c-4dd8-8579-2eeb46519579"',
        '    controltower_ai_sbx_unity_catalog_schema = "6336c56e-285f-445c-99b0-5c152e473b14"',
    ]
    assert added == [
        '    controltower_sbx_unity_catalog_schema      = "40f02f32-e60c-4dd8-8579-2eeb46519579"',
        '    controltower_ai_sbx_unity_catalog_schema   = "6336c56e-285f-445c-99b0-5c152e473b14"',
        '    wealth_management_sbx_unity_catalog_schema = "u-2"',
    ]


def test_an_already_registered_key_is_a_no_op():
    patch = stack_ids.register_stack_id_patch(stack_ids.UNITY_CATALOG, "controltower_unity_catalog", "new-uuid")

    assert patch(BEFORE) == BEFORE


def test_a_missing_group_is_added_with_its_comment_before_the_closing_brace():
    text = 'globals {\n  stack_ids = {\n    network_foundation = "nf"\n  }\n}\n'
    patch = stack_ids.register_stack_id_patch(stack_ids.WORKSPACE_FOLDER, "finance_workspace_folder", "u-3")

    assert patch(text) == (
        "globals {\n"
        "  stack_ids = {\n"
        '    network_foundation = "nf"\n'
        '    # business_domain/workspace_folder stacks: "<domain_name>_workspace_folder"\n'
        '    finance_workspace_folder = "u-3"\n'
        "  }\n"
        "}\n"
    )


def test_workspace_group_does_not_match_the_workspace_folder_group():
    text = (
        "  stack_ids = {\n"
        '    # business_domain/workspace_folder stacks: "<domain_name>_workspace_folder"\n'
        '    a_workspace_folder = "1"\n'
        "  }\n"
    )
    after = stack_ids.register_stack_id_patch(stack_ids.WORKSPACE, "b_workspace", "2")(text)

    assert after.splitlines()[-3:] == [
        '    # business_domain/workspace stacks: "<domain_name>_workspace"',
        '    b_workspace = "2"',
        "  }",
    ]


LABELED_GLOBALS = (
    'globals "aws_backend" {\n'
    '  region = "${global.aws.region}"\n'
    "}\n"
    "\n"
    "# The keys here MUST match terramate.stack.name, e.g. {\n"
    'globals "stack_ids" "sbx" {\n'
    '  network_foundation = "ba74849b-ff01-4701-a1f0-8156f21718de"\n'
    '  # business_domain/workspace stacks: "<domain_name>_workspace"\n'
    '  controltower_workspace = "3f23efb8-da83-478a-bdb3-23d6b78b38a3"\n'
    '  # business_domain/workspace_folder stacks: "<domain_name>_workspace_folder"\n'
    '  controltower_workspace_folder = "7345ad70-d74e-415c-b4f7-2864dd9c656a"\n'
    "}\n"
)


def test_a_labeled_globals_stack_ids_block_is_edited_in_place():
    after = stack_ids.register_stack_id_patch(stack_ids.WORKSPACE, "justtesting_workspace", "u-5")(LABELED_GLOBALS)

    removed, added = _changed_lines(LABELED_GLOBALS, after)
    assert removed == []
    assert added == ['  justtesting_workspace  = "u-5"']
    lines = after.splitlines()
    assert lines[lines.index('  controltower_workspace = "3f23efb8-da83-478a-bdb3-23d6b78b38a3"') + 1] == (
        '  justtesting_workspace  = "u-5"'
    )


def test_a_missing_group_is_added_to_the_end_of_a_labeled_globals_block():
    after = stack_ids.register_stack_id_patch(stack_ids.UNITY_CATALOG, "justtesting_unity_catalog", "u-6")(
        LABELED_GLOBALS
    )

    assert after.splitlines()[-3:] == [
        '  # data_domain/unity_catalog stacks: "<domain_name>_unity_catalog"',
        '  justtesting_unity_catalog = "u-6"',
        "}",
    ]


def test_a_config_without_a_stack_ids_block_is_rejected():
    patch = stack_ids.register_stack_id_patch(stack_ids.WORKSPACE, "finance_workspace", "u-4")

    with pytest.raises(ValueError, match="stack_ids"):
        patch("globals {\n}\n")
