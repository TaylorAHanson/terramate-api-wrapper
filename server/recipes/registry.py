"""The per-type Recipe registry (architecture.md §15.1's "Registry").

Adding a type means adding an entry here: a new Recipe instance (and its
Pydantic request-envelope model, wired into the `/v1/requests` route's
type-discriminated Union — see server.routes.requests).
"""
from __future__ import annotations

from server.recipes.framework import Recipe
from server.recipes.schema import SchemaRecipe
from server.recipes.workspace import WorkspaceRecipe

RECIPES: dict[str, Recipe] = {
    "schema": SchemaRecipe(),
    "workspace": WorkspaceRecipe(),
}
