"""The reconcile loop (architecture.md §4.2, §5, §6).

`tick()` is the orchestrator's synchronous entry point — the same one a timer
would call in production and a test calls directly for deterministic control
(architecture.md §14, "operator wants a synchronous advance/tick entry
point"). Each pass: poll GitHub for every Step already awaiting approval,
check whether any merged Step's apply-derived Outputs have landed yet, then
claim and open PRs for every currently-runnable queued Step — in that order,
so a Step that just became runnable this pass is claimed the same tick.

v1 still collapses `pr_open`/`planning`/`planned` into one reconcile pass,
ending at `awaiting_approval` — there is no persisted state for "PR open,
plan not back yet." Against the real fixture repo (#22) that collapse is
absorbed by `RealGitHubClient.get_plan` itself, which blocks (bounded, with
a timeout) until the real `terraform-plan` check run lands, rather than the
orchestrator polling across ticks — un-collapsing these into their own
tracked sub-states remains future work (see #19).

`merged` -> `applying` -> `applied` does **not** collapse the same way for a
Step with apply-derived Outputs (#20): a merged Step is held at `applying`
until its expected Outputs are present in Lakebase (ADR-0002 — written by the
Step's GitHub Action in production, simulated by a direct Lakebase write in
Seam 1 tests, since no real Action exists yet), so a dependent Step's PR is
never opened with a blank reference (architecture.md §14). A Step with no
Outputs to wait on still collapses straight to `applied` on merge.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.github_client import GitHubClient
from server.models import Output, ProvisioningRequest, Step
from server.recipes.framework import AddFile, EditFile, FileEdit
from server.recipes.registry import RECIPES

_FAILED_STEP_STATUSES = {"plan_failed", "apply_failed", "rejected"}

# A request halted here (failed, by a Step; cancelled, by an operator) never
# resumes (architecture.md §6, "halt-no-rollback") — none of its Steps are
# claimed or advanced further, even a queued Step with no dependency relation
# to whatever halted the request (#21).
TERMINAL_REQUEST_STATUSES = {"succeeded", "failed", "cancelled"}


def tick(session: Session, github_client: GitHubClient) -> None:
    """Advance every in-flight Step by one reconciliation pass."""
    _advance_awaiting_approval(session, github_client)
    _advance_applying(session)
    while _claim_and_open_next(session, github_client):
        pass


def _claim_and_open_next(session: Session, github_client: GitHubClient) -> bool:
    """Claim and open the PR for the next runnable queued Step, if any.

    A worker "claims" a Step by recording claimed_at/claimed_by, but that
    alone never moves it out of `queued` — only a successfully-opened PR
    does. So if this crashes right after claiming but before the PR is
    open, the Step is simply still `queued` on the next tick and this
    query re-selects (and re-claims) it, rather than it getting stuck.
    """
    queued_steps = session.scalars(
        select(Step)
        .where(Step.status == "queued")
        .order_by(Step.ordinal)
        .with_for_update(skip_locked=True)
    ).all()

    step = next(
        (
            s
            for s in queued_steps
            if s.request.status not in TERMINAL_REQUEST_STATUSES and _dependencies_applied(session, s)
        ),
        None,
    )
    if step is None:
        return False

    step.claimed_at = datetime.now(timezone.utc)
    step.claimed_by = f"{socket.gethostname()}:{os.getpid()}"

    body = (
        f"Automated by the provisioning API for step `{step.key}` "
        f"of request `{step.request_id}`."
    )
    resolved = _resolve_consumes(session, step)
    if resolved:
        lines = "\n".join(
            f"- `${{steps.{ref['step_key']}.outputs.{ref['output_name']}}}` = `{value}`"
            for ref, value in resolved
        )
        body += f"\n\nResolved inputs:\n{lines}"

    pr = github_client.open_pull_request(
        branch_name=f"provision/{step.request_id}/{step.key}",
        base_branch="main",
        title=f"{step.request.type}: {step.key}",
        body=body,
        edits=_resolve_edits(step, resolved),
    )
    step.pr_number = pr.number
    step.pr_url = pr.url
    step.plan_ref = github_client.get_plan(pr.number)
    step.status = "awaiting_approval"

    _roll_up_request(session, step.request_id)
    session.commit()
    return True


def _dependencies_applied(session: Session, step: Step) -> bool:
    if not step.depends_on:
        return True
    applied_ids = session.scalars(
        select(Step.id).where(Step.id.in_(step.depends_on), Step.status == "applied")
    ).all()
    return set(applied_ids) == set(step.depends_on)


def _resolve_consumes(session: Session, step: Step) -> list[tuple[dict, Any]]:
    """Resolve each of this Step's OutputRefs (§5.1's `${steps.<key>.outputs.<name>}`)
    from Lakebase, by reference — never copied ahead of time. Safe to call once
    the Step is runnable: `_dependencies_applied` already guarantees the
    referenced Step is `applied`, which in turn guarantees its Outputs exist
    (see `_produced_outputs_present`).
    """
    resolved = []
    for ref in step.consumes:
        dep_step_id = session.scalars(
            select(Step.id).where(Step.request_id == step.request_id, Step.key == ref["step_key"])
        ).one()
        output = session.scalars(
            select(Output).where(Output.step_id == dep_step_id, Output.key == ref["output_name"])
        ).one()
        resolved.append((ref, output.value))
    return resolved


def _resolve_edits(step: Step, resolved: list[tuple[dict, Any]]) -> list[FileEdit]:
    """Rebuild the Step's `bundle_edits` from its Recipe (§15.1) and substitute
    each `${steps.<key>.outputs.<name>}` placeholder (already resolved above)
    with its real value, so the real GitHubClient commits actual apply-derived
    content rather than the placeholder token (#22 — this was previously only
    a PR-body stand-in, see #20).

    Recipes are pure functions of `params` (persisted on the request), so
    rebuilding here reproduces the exact same StepSpec that was built at
    request-creation time — nothing here is re-decided.
    """
    recipe = RECIPES[step.request.type]
    playbook = recipe.build_from_params_dict(step.request.params)
    spec = next(s for s in playbook.steps if s.key == step.key)
    substitutions = {
        f"${{steps.{ref['step_key']}.outputs.{ref['output_name']}}}": value for ref, value in resolved
    }
    return [_substitute_edit(edit, substitutions) for edit in spec.bundle_edits]


def _substitute_edit(edit: FileEdit, substitutions: dict[str, Any]) -> FileEdit:
    if isinstance(edit, AddFile):
        return AddFile(edit.path, _substitute_text(edit.content, substitutions))
    if isinstance(edit, EditFile):
        original_patch = edit.patch
        return EditFile(
            edit.path,
            lambda document: _substitute_structure(original_patch(document), substitutions),
        )
    raise TypeError(f"Unknown FileEdit type: {type(edit)!r}")


def _substitute_text(text: str, substitutions: dict[str, Any]) -> str:
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, str(value))
    return text


def _substitute_structure(value: Any, substitutions: dict[str, Any]) -> Any:
    """Recurse through an `EditFile.patch`-mutated dict/list, substituting
    placeholders in every string leaf (see `_substitute_edit`)."""
    if isinstance(value, str):
        return _substitute_text(value, substitutions)
    if isinstance(value, dict):
        return {k: _substitute_structure(v, substitutions) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_structure(v, substitutions) for v in value]
    return value


def _produced_outputs_present(session: Session, step: Step) -> bool:
    if not step.produces:
        return True
    captured_keys = session.scalars(
        select(Output.key).where(Output.step_id == step.id, Output.key.in_(step.produces))
    ).all()
    return set(captured_keys) == set(step.produces)


def _advance_awaiting_approval(session: Session, github_client: GitHubClient) -> None:
    steps = session.scalars(select(Step).where(Step.status == "awaiting_approval")).all()
    for step in steps:
        if step.request.status in TERMINAL_REQUEST_STATUSES:
            continue
        pr_status = github_client.get_pull_request_status(step.pr_number)
        if pr_status.merged:
            step.status = "applied" if _produced_outputs_present(session, step) else "applying"
        elif pr_status.closed:
            step.status = "rejected"
        else:
            continue
        _roll_up_request(session, step.request_id)
    session.commit()


def _advance_applying(session: Session) -> None:
    """A Step parked at `applying` is waiting on its GitHub Action's Lakebase
    output write (ADR-0002); nothing to poll here (that's `_advance_awaiting_
    approval`'s job) — just check whether the write has landed since the last
    tick, per architecture.md §14's "hold rather than open the next PR with a
    blank reference."
    """
    steps = session.scalars(select(Step).where(Step.status == "applying")).all()
    for step in steps:
        if _produced_outputs_present(session, step):
            step.status = "applied"
            _roll_up_request(session, step.request_id)
    session.commit()


def _roll_up_request(session: Session, request_id: str) -> None:
    request = session.get(ProvisioningRequest, request_id)
    if request.status == "cancelled":
        # An operator cancellation (#21) isn't derivable from Step statuses
        # the way the other rollup outcomes below are, so it's not
        # recomputed away.
        return
    steps = request.steps
    if any(s.status in _FAILED_STEP_STATUSES for s in steps):
        request.status = "failed"
    elif all(s.status == "applied" for s in steps):
        request.status = "succeeded"
    elif any(s.status == "awaiting_approval" for s in steps):
        request.status = "awaiting_approval"
    else:
        request.status = "in_progress"
