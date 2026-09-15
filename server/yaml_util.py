"""YAML serialization and deserialization utilities.

Provides round-trip styling preservation:
- Indents list items under mapping keys by 2 spaces (rather than PyYAML's
  default flush/indentless list formatting).
- Preserves double quotes on scalar strings (e.g. `"/src/..."` and `"controltower"`).
- Emits newly appended strings wrapped in `QuotedStr` as double-quoted scalars,
  ensuring PR diffs are reviewable and match customer repo styling exactly.
"""
from __future__ import annotations

from typing import Any

import yaml


class QuotedStr(str):
    """A string subclass that signals the YAML dumper to emit double quotes."""

    pass


def _quoted_str_representer(dumper: yaml.Dumper, data: Any) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


class IndentedYamlDumper(yaml.SafeDumper):
    """YAML dumper that indents block sequences under mapping keys by 2 spaces."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow=flow, indentless=False)


IndentedYamlDumper.add_representer(QuotedStr, _quoted_str_representer)
yaml.SafeDumper.add_representer(QuotedStr, _quoted_str_representer)


class PreservingYamlLoader(yaml.SafeLoader):
    """YAML loader that preserves double-quoted string styles using QuotedStr."""

    pass


def _construct_preserving_str(loader: yaml.Loader, node: yaml.ScalarNode) -> Any:
    val = loader.construct_scalar(node)
    if node.style == '"':
        return QuotedStr(val)
    return val


PreservingYamlLoader.add_constructor("tag:yaml.org,2002:str", _construct_preserving_str)


def load_yaml(content: str) -> dict[str, Any]:
    """Parse YAML content, preserving double-quoted strings as QuotedStr."""
    return yaml.load(content, Loader=PreservingYamlLoader) or {}


def dump_yaml(data: Any) -> str:
    """Dump Python data structure to YAML with 2-space indented lists and QuotedStr preserved."""
    return yaml.dump(data, Dumper=IndentedYamlDumper, default_flow_style=False, sort_keys=False)
