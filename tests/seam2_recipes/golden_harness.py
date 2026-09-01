"""The golden-file harness (architecture.md §15.2): assert a Recipe's
`build(params) -> Playbook` produces an exact, checked-in Playbook + file-edit
shape. No HTTP, no database, no GitHub — pure and fast.

Set UPDATE_GOLDEN=1 to (re)write the golden file from the current output,
the way a recipe author accepts an intentional change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from server.recipes.framework import AddFile, EditFile, FileEdit, Playbook


def _file_edit_to_dict(edit: FileEdit) -> dict:
    if isinstance(edit, AddFile):
        return {"type": "add_file", "path": edit.path, "content": edit.content}
    if isinstance(edit, EditFile):
        return {"type": "edit_file", "path": edit.path}
    raise TypeError(f"Unknown FileEdit type: {type(edit)!r}")


def playbook_to_dict(playbook: Playbook) -> dict:
    return {
        "steps": [
            {
                "key": step.key,
                "depends_on": list(step.depends_on),
                "produces": list(step.produces),
                "consumes": [
                    {"step_key": ref.step_key, "output_name": ref.output_name}
                    for ref in step.consumes
                ],
                "bundle_edits": [_file_edit_to_dict(edit) for edit in step.bundle_edits],
            }
            for step in playbook.steps
        ]
    }


def assert_matches_golden(playbook: Playbook, golden_path: Path) -> None:
    actual = playbook_to_dict(playbook)

    if os.environ.get("UPDATE_GOLDEN") or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        return

    expected = json.loads(golden_path.read_text())
    assert actual == expected, (
        f"Playbook for {golden_path.name} no longer matches its golden file. "
        f"If this is intentional, rerun with UPDATE_GOLDEN=1.\n"
        f"expected: {expected}\nactual:   {actual}"
    )
