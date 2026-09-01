"""Proves the golden-file harness end to end.

`_EchoRecipe` is a throwaway fixture recipe, not one of the real per-type
recipes (`workspace`, `schema`) — those, and their own golden cases, land in
#18. This only needs to exist so the harness itself is exercised now rather
than first proven under a real recipe's schedule pressure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from server.recipes.framework import AddFile, Playbook, Recipe, StepSpec
from tests.seam2_recipes.golden_harness import assert_matches_golden

GOLDEN_DIR = Path(__file__).parent / "golden"


class _EchoRecipe(Recipe):
    type = "echo"

    def build(self, params: dict[str, Any]) -> Playbook:
        return Playbook(
            steps=[
                StepSpec(
                    key="echo",
                    bundle_edits=[
                        AddFile(
                            path=f"stacks/echo/{params['name']}.yaml",
                            content=f"name: {params['name']}\n",
                        )
                    ],
                ),
            ]
        )


def test_echo_recipe_matches_golden_playbook():
    playbook = _EchoRecipe().build({"name": "demo"})
    assert_matches_golden(playbook, GOLDEN_DIR / "echo_case.json")
