"""Registering a new stack's id in the environment config (ADR-0005).

Every stack a Recipe creates needs a `<key> = "<uuid>"` entry in the stack ids
block of `src/configs/<env>/<env>_config.tm.hcl`; the terramate repo uses it as
the prefix for that stack's state files. The block is either a labeled globals
block (`globals "stack_ids" "<env>" { ... }`) or a `stack_ids = { ... }`
attribute. Entries are grouped under a `# <family>/<unit> stacks: "<naming>"`
comment, e.g.:

    globals "stack_ids" "sbx" {
      network_foundation = "ba74849b-..."
      # business_domain/workspace stacks: "<domain_name>_workspace"
      controltower_workspace = "3f23efb8-..."
    }

There is no HCL round-trip serializer available, so this is a targeted text
edit: the new line is appended to its group, and only that group's `=` column
is re-aligned (what `terraform fmt` would produce). Everything else in the
file is left byte-for-byte untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from server.recipes.framework import EditText

_BLOCK_START = re.compile(
    r'^(?P<indent>\s*)(?:globals\s+"stack_ids"(?:\s+"[^"]*")*|stack_ids\s*=)\s*\{\s*$'
)
_ATTRIBUTE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_\-]+)\s*=\s*(?P<value>.*)$")
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


@dataclass(frozen=True)
class StackGroup:
    """One commented group of entries inside `stack_ids`."""

    path: str
    naming: str

    @property
    def comment(self) -> str:
        return f'# {self.path} stacks: "{self.naming}"'

    def matches(self, line: str) -> bool:
        return line.strip().startswith(f"# {self.path} stacks")


WORKSPACE = StackGroup("business_domain/workspace", "<domain_name>_workspace")
WORKSPACE_FOLDER = StackGroup("business_domain/workspace_folder", "<domain_name>_workspace_folder")
UNITY_CATALOG = StackGroup("data_domain/unity_catalog", "<domain_name>_unity_catalog")
UNITY_CATALOG_SCHEMA = StackGroup("data_domain/unity_catalog_schema", "<catalog_name>_unity_catalog_schema")


def env_config_path(environment: str) -> str:
    return f"src/configs/{environment}/{environment}_config.tm.hcl"


def register_stack_id(environment: str, group: StackGroup, key: str, stack_uuid: str) -> EditText:
    """The `EditText` that adds `key = "<stack_uuid>"` to `environment`'s config."""
    return EditText(env_config_path(environment), register_stack_id_patch(group, key, stack_uuid))


def register_stack_id_patch(group: StackGroup, key: str, stack_uuid: str) -> Callable[[str], str]:
    def patch(text: str) -> str:
        lines = text.splitlines()
        start, end = _find_block(lines)
        body = range(start + 1, end)

        if any(_attribute_key(lines[i]) == key for i in body):
            return text

        entry_indent = next(
            (_ATTRIBUTE.match(lines[i])["indent"] for i in body if _ATTRIBUTE.match(lines[i])),
            _BLOCK_START.match(lines[start])["indent"] + "  ",
        )
        entry = f'{entry_indent}{key} = "{stack_uuid}"'

        header = next((i for i in body if group.matches(lines[i])), None)
        if header is None:
            lines[end:end] = [f"{entry_indent}{group.comment}", entry]
        else:
            last = header
            while last + 1 < end and _attribute_key(lines[last + 1]) is not None:
                last += 1
            lines.insert(last + 1, entry)
            lines[header + 1 : last + 2] = _align(lines[header + 1 : last + 2])

        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    return patch


def _find_block(lines: list[str]) -> tuple[int, int]:
    """(index of the stack ids block's opening line, index of its closing `}` line)."""
    start = next((i for i, line in enumerate(lines) if _BLOCK_START.match(line)), None)
    if start is None:
        raise ValueError(
            'no `globals "stack_ids" "<env>" {` or `stack_ids = {` block found in the environment config'
        )
    depth = 0
    for i in range(start, len(lines)):
        unquoted = _QUOTED.sub("", lines[i].split("#", 1)[0])
        depth += unquoted.count("{") - unquoted.count("}")
        if depth == 0:
            return start, i
    raise ValueError("unterminated stack ids block in the environment config")


def _attribute_key(line: str) -> str | None:
    match = _ATTRIBUTE.match(line)
    return match["key"] if match else None


def _align(group_lines: list[str]) -> list[str]:
    matches = [_ATTRIBUTE.match(line) for line in group_lines]
    width = max(len(m["key"]) for m in matches)
    return [f'{m["indent"]}{m["key"].ljust(width)} = {m["value"]}' for m in matches]
