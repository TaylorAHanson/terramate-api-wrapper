"""The reconcile loop (architecture.md §4.2, §5, §6).

`tick()` is the orchestrator's synchronous entry point — the same one a timer
would call in production and a test calls directly for deterministic control
(architecture.md §14, "operator wants a synchronous advance/tick entry
point"). Each pass: poll GitHub for every Step already awaiting approval,
check whether any merged Step's apply-derived Outputs have landed yet, then
claim and open PRs for every currently-runnable queued Step — in that order,
so a Step that just became runnable this pass is claimed the same tick.

v1 has no live GitHub-Actions check polling yet — that lands with the
fixture-repo integration (Seam 3, #22). Until then, a Step's plan is
available synchronously once its PR is open (so `pr_open`/`planning`/
`planned` collapse into one reconcile pass, ending at `awaiting_approval`) —
there is no real signal yet to gate that sub-transition on.

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

_FAILED_STEP_STATUSES = {"plan_failed", "apply_failed", "rejected"}


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

    step = next((s for s in queued_steps if _dependencies_applied(session, s)), None)
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
    steps = request.steps
    if any(s.status in _FAILED_STEP_STATUSES for s in steps):
        request.status = "failed"
    elif all(s.status == "applied" for s in steps):
        request.status = "succeeded"
    elif any(s.status == "awaiting_approval" for s in steps):
        request.status = "awaiting_approval"
    else:
        request.status = "in_progress"
