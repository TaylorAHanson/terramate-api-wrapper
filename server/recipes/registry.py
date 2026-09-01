"""The per-type Recipe registry (architecture.md §15.1's "Registry").

Adding a type means adding an entry here: a new Recipe instance (and its
Pydantic request-envelope model, wired into the `/v1/requests` route). Today
this holds a single entry (`schema`, #18); `workspace` lands in #20.
"""
from __future__ import annotations

from server.recipes.framework import Recipe
from server.recipes.schema import SchemaRecipe

RECIPES: dict[str, Recipe] = {
    "schema": SchemaRecipe(),
}
