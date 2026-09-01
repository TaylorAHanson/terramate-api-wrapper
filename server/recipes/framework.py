"""The Recipe framework (architecture.md §15.1, ADR-0001).

`Playbook`, `StepSpec`, `AddFile`, `EditFile`, and `OutputRef` are provided
once here and used by every per-type Recipe; a Recipe is a generator —
`build(params) -> Playbook` — deployed with the app. No concrete Recipe (e.g.
`workspace`, `schema`) ships in this ticket; those, and the `RECIPES`
registry, land in #18. This module exists so Seam 2's golden-file harness has
a real interface to test against.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class AddFile:
    """A new bundle file's full content, rendered from params."""

    path: str
    content: str


@dataclass(frozen=True)
class EditFile:
    """A structured mutation of an existing bundle file's parsed YAML.

    `patch` receives the file's parsed YAML (a dict) and returns the mutated
    dict — parse -> mutate -> serialize, never text munging, so the resulting
    PR diff stays reviewable.
    """

    path: str
    patch: Callable[[dict[str, Any]], dict[str, Any]]


FileEdit = AddFile | EditFile


@dataclass(frozen=True)
class OutputRef:
    """A reference to an earlier Step's apply-derived output.

    Resolved by the engine — via the `${steps.<step_key>.outputs.<name>}`
    placeholder — from Lakebase before the consuming Step's PR is opened.
    """

    step_key: str
    output_name: str


@dataclass(frozen=True)
class StepSpec:
    key: str
    bundle_edits: Sequence[FileEdit] = field(default_factory=tuple)
    depends_on: Sequence[str] = field(default_factory=tuple)
    consumes: Sequence[OutputRef] = field(default_factory=tuple)
    produces: Sequence[str] = field(default_factory=tuple)
    preflight: Sequence[Callable[[], None]] = field(default_factory=tuple)
    postflight: Sequence[Callable[[], None]] = field(default_factory=tuple)


@dataclass(frozen=True)
class Playbook:
    steps: Sequence[StepSpec] = field(default_factory=tuple)


class Recipe(ABC):
    """The per-type tribal knowledge, expressed as imperative code (ADR-0001)."""

    type: str

    @abstractmethod
    def build(self, params: Any) -> Playbook: ...
