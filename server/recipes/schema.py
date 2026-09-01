"""The `schema` Recipe (architecture.md §15.1): "find the right catalog, then
add the schema to it" — a single Step, no apply-derived Outputs, every value
API-known up front.

`locate_catalog` is a deterministic fixture-repo path convention rather than
a real lookup against repo content — the real lookup lands with the live
fixture-repo integration (#22); until then this keeps the golden-file harness
(Seam 2) exercisable without a GitHub call.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel

from server.recipes.framework import EditFile, Playbook, Recipe, StepSpec


class SchemaParams(BaseModel):
    catalog: str
    name: str
    owner: str
    comment: str | None = None


class SchemaProvisioningRequest(BaseModel):
    """The `POST /v1/requests` envelope for `type: "schema"`.

    Published as-is in `/openapi.json` for now; once a second type
    (`workspace`, #20) lands this becomes one member of a `type`-discriminated
    Union rather than the sole request-body model.
    """

    type: Literal["schema"]
    params: SchemaParams


def locate_catalog(catalog: str) -> str:
    """The bundle file that holds `catalog`'s schema list."""
    return f"stacks/catalogs/{catalog}/catalog.tm.yaml"


def add_schema_patch(
    name: str, owner: str, comment: str | None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A structured YAML patch that appends one schema entry to a catalog file."""

    def patch(document: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": name, "owner": owner}
        if comment is not None:
            entry["comment"] = comment
        return {**document, "schemas": [*document.get("schemas", []), entry]}

    return patch


class SchemaRecipe(Recipe):
    type = "schema"

    def build(self, params: SchemaParams) -> Playbook:
        catalog_file = locate_catalog(params.catalog)
        return Playbook(
            steps=[
                StepSpec(
                    key="add-schema",
                    bundle_edits=[
                        EditFile(
                            catalog_file,
                            add_schema_patch(params.name, params.owner, params.comment),
                        ),
                    ],
                    # No depends_on, no consumes, no produces — one PR, no value-passing.
                ),
            ]
        )
