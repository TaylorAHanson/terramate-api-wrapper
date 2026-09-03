"""The reconcile loop (architecture.md §4.2, §5, §6).

`tick()` is the orchestrator's synchronous entry point — the same one a timer
would call in production and a test calls directly for deterministic control
(architecture.md §14, "operator wants a synchronous advance/tick entry
point"). Each pass: poll GitHub for every Step already awaiting approval,
check whether any merged Step's apply-derived Outputs have landed yet, then
claim and open PRs for every currently-runnable queued Step — in that order,
so a Step that just became runnable this pass is claimed the same tick.

v1 still collapses `planning`/`planned` into `pr_open`'s wait for the plan —
`pr_open` is the persisted "PR open, plan not back yet" sub-state #19 flagged
as future work, added by #45 so a Step whose plan is slow to land no longer
blocks the tick from claiming and opening PRs for other runnable Steps.
`_advance_pr_open` re-checks `GitHubClient.get_plan` once per tick (never
blocking — see server/github_client.py) until it succeeds, then the Step
moves to `awaiting_approval`.

`merged` -> `applying` -> `applied` does **not** collapse the same way for a
Step with apply-derived Outputs (#20): a merged Step is held at `applying`
until its expected Outputs are present in Lakebase (ADR-0002 — written by the
Step's GitHub Action in production, simulated by a direct Lakebase write in
Seam 1 tests, since no real Action exists yet), so a dependent Step's PR is
never opened with a blank reference (architecture.md §14). A Step with no
Outputs to wait on still collapses straight to `applied` on merge.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.github_client import GitHubClient, PlanNotReadyError, correlate
from server.models import Output, ProvisioningRequest, Step
from server.recipes.framework import AddFile, EditFile, FileEdit, StepSpec
from server.recipes.registry import RECIPES

logger = logging.getLogger(__name__)

_FAILED_STEP_STATUSES = {"plan_failed", "apply_failed", "rejected"}

# A request halted here (failed, by a Step; cancelled, by an operator) never
# resumes (architecture.md §6, "halt-no-rollback") — none of its Steps are
# claimed or advanced further, even a queued Step with no dependency relation
# to whatever halted the request (#21).
TERMINAL_REQUEST_STATUSES = {"succeeded", "failed", "cancelled"}


def _transition(step: Step, to_status: str) -> None:
    """The single path every Step status change goes through.

    Records `status_changed_at` (so the stuck check knows how long a Step has
    been held — see `_flag_stuck_steps`), clears any `stuck` flag (a Step that
    moved is no longer stuck in its old state, #43), and emits the one canonical
    `step_transition` line an operator can grep to trace a Step's whole
    lifecycle. Correlating ids (`request_id`, step `key`/`ordinal`) are on every
    line (#41). Mutates the Step in place; the caller commits.
    """
    from_status = step.status
    step.status = to_status
    step.status_changed_at = datetime.now(timezone.utc)
    step.stuck = False
    logger.info(
        "step_transition request_id=%s step=%s ordinal=%s from=%s to=%s",
        step.request_id,
        step.key,
        step.ordinal,
        from_status,
        to_status,
    )


def tick(session: Session, github_client: GitHubClient, stuck_threshold_seconds: float | None = None) -> None:
    """Advance every in-flight Step by one reconciliation pass.

    `stuck_threshold_seconds` overrides the configured
    `STEP_STUCK_THRESHOLD_SECONDS` (tests pass it to drive stuck detection
    deterministically); `None` reads it from settings.
    """
    _advance_awaiting_approval(session, github_client)
    _advance_applying(session)
    while _claim_and_open_next(session, github_client):
        pass
    _advance_pr_open(session, github_client)
    _flag_stuck_steps(session, stuck_threshold_seconds)


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
    logger.debug(
        "step_claimed request_id=%s step=%s ordinal=%s claimed_by=%s",
        step.request_id,
        step.key,
        step.ordinal,
        step.claimed_by,
    )

    spec = _build_step_spec(step)
    try:
        _run_hooks(spec.preflight, step, "preflight")
    except Exception:
        logger.exception(
            "step_preflight_failed request_id=%s step=%s ordinal=%s",
            step.request_id,
            step.key,
            step.ordinal,
        )
        _transition(step, "plan_failed")
        _roll_up_request(session, step.request_id)
        session.commit()
        return True

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

    with correlate(request_id=step.request_id, step_key=step.key, ordinal=step.ordinal):
        pr = github_client.open_pull_request(
            branch_name=f"provision/{step.request_id}/{step.key}",
            base_branch="main",
            title=f"{step.request.type}: {step.key}",
            body=body,
            edits=_resolve_edits(step, resolved),
        )
    step.pr_number = pr.number
    step.pr_url = pr.url
    logger.info(
        "pr_opened request_id=%s step=%s ordinal=%s pr_number=%s pr_url=%s",
        step.request_id,
        step.key,
        step.ordinal,
        pr.number,
        pr.url,
    )
    # The plan wait is deliberately *not* done here (#45) — `get_plan` never
    # blocks, but this Step's plan may still not be back yet, and a
    # single `while _claim_and_open_next(): pass` pass must be able to move on
    # to the next runnable queued Step regardless. `_advance_pr_open` is what
    # picks this Step back up, this same tick and every tick after.
    _transition(step, "pr_open")

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


def _build_step_spec(step: Step) -> StepSpec:
    """Rebuild the Step's `StepSpec` from its Recipe (§15.1) and persisted
    `params` — including its `preflight`/`postflight` hook callables, which
    are never persisted themselves (they can't be: they're code, not data),
    so the engine re-derives them here rather than storing them.

    Recipes are pure functions of `params`, so rebuilding here reproduces the
    exact same StepSpec that was built at request-creation time — nothing
    here is re-decided.
    """
    recipe = RECIPES[step.request.type]
    playbook = recipe.build_from_params_dict(step.request.params)
    return next(s for s in playbook.steps if s.key == step.key)


def _run_hooks(hooks: Sequence[Callable[[], None]], step: Step, hook_name: str) -> None:
    for hook in hooks:
        logger.debug(
            "step_hook_invoked request_id=%s step=%s ordinal=%s hook=%s",
            step.request_id,
            step.key,
            step.ordinal,
            hook_name,
        )
        hook()


def _resolve_edits(step: Step, resolved: list[tuple[dict, Any]]) -> list[FileEdit]:
    """Substitute each `${steps.<key>.outputs.<name>}` placeholder (already
    resolved above) with its real value, so the real GitHubClient commits
    actual apply-derived content rather than the placeholder token (#22 —
    this was previously only a PR-body stand-in, see #20).
    """
    spec = _build_step_spec(step)
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


def _advance_pr_open(session: Session, github_client: GitHubClient) -> None:
    """Re-check each PR-open Step's plan, one non-blocking attempt per tick
    (#45) — `PlanNotReadyError` just means "still not back, try again next
    tick," so one stalled Step is skipped rather than stalling the loop for
    every other PR-open Step this same tick.
    """
    steps = session.scalars(select(Step).where(Step.status == "pr_open")).all()
    for step in steps:
        if step.request.status in TERMINAL_REQUEST_STATUSES:
            continue
        try:
            with correlate(request_id=step.request_id, step_key=step.key, ordinal=step.ordinal):
                plan = github_client.get_plan(step.pr_number)
        except PlanNotReadyError:
            continue
        step.plan_ref = plan
        _transition(step, "awaiting_approval")
        _roll_up_request(session, step.request_id)
    session.commit()


def _advance_awaiting_approval(session: Session, github_client: GitHubClient) -> None:
    steps = session.scalars(select(Step).where(Step.status == "awaiting_approval")).all()
    for step in steps:
        if step.request.status in TERMINAL_REQUEST_STATUSES:
            continue
        with correlate(request_id=step.request_id, step_key=step.key, ordinal=step.ordinal):
            pr_status = github_client.get_pull_request_status(step.pr_number)
        if pr_status.merged:
            if _produced_outputs_present(session, step):
                _transition_to_applied(session, step)
                continue
            next_status = "applying"
        elif pr_status.closed:
            next_status = "rejected"
            logger.warning(
                "step_rejected request_id=%s step=%s ordinal=%s pr_number=%s (PR closed unmerged)",
                step.request_id,
                step.key,
                step.ordinal,
                step.pr_number,
            )
        else:
            continue
        _transition(step, next_status)
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
            _transition_to_applied(session, step)
    session.commit()


def _transition_to_applied(session: Session, step: Step) -> None:
    """Run this Step's postflight hooks and land it at `applied` — or, if a
    hook raises, at `apply_failed`. The single site both `merged` collapse
    paths funnel through (an immediate merged->applied in `_advance_awaiting_
    approval` for a Step with no Outputs to wait on, and a delayed one via
    `_advance_applying` once its Outputs land) so postflight always runs
    exactly once, right after the Step's apply-derived Outputs (if any) have
    landed.
    """
    spec = _build_step_spec(step)
    try:
        _run_hooks(spec.postflight, step, "postflight")
    except Exception:
        logger.exception(
            "step_postflight_failed request_id=%s step=%s ordinal=%s",
            step.request_id,
            step.key,
            step.ordinal,
        )
        _transition(step, "apply_failed")
    else:
        _transition(step, "applied")
    _roll_up_request(session, step.request_id)


def record_apply_result(
    session: Session,
    step: Step,
    applied: bool,
    outputs: dict[str, Any],
    tf_console: str,
) -> None:
    """Persist a merged Step's reported apply result (ADR-0003 — the future
    `PUT .../steps/{n}/outputs` calls straight into this). #54 is the
    HTTP-free persistence seam: today's only caller is a test; the endpoint
    itself is #55.

    `tf_console` is always stored, applied or not. `applied=False` moves the
    Step to `apply_failed` through `_transition` (the #41 log line +
    `status_changed_at`) — a status that didn't exist before this: a Step
    previously only ever reached `applying` after merge, with no way to
    report that the apply itself failed.

    `applied=True` upserts each of `outputs` into `Output`, keyed by
    `(step_id, key)`, by overwriting an existing row's `value` in place
    rather than re-`add`ing one — a plain insert would trip
    `uq_output_step_key` on a retried report, so this makes a duplicate
    report with identical values a no-op. It then re-checks the same
    `_produced_outputs_present` gate `_advance_applying` polls every tick and,
    if the Step's full `produces` set is now present, advances it to
    `applied` immediately via `_transition_to_applied` — **this seam
    transitions directly**, rather than leaving it for the next reconcile
    tick, so a caller never needs a follow-up `tick()` to see the Step land.
    A partial report (not every `produces` key present yet) leaves the Step
    held at `applying`, exactly as a partial/late ADR-0002-style write would.

    Both branches only transition while the Step is still `applying` — a
    repeated report against an already-`applied`/`apply_failed` Step still
    updates `tf_console`/outputs but does not re-transition (no re-run
    postflight hooks, no re-logged transition), keeping a retried report
    idempotent rather than merely non-erroring.
    """
    step.tf_console = tf_console

    if not applied:
        if step.status == "applying":
            _transition(step, "apply_failed")
            _roll_up_request(session, step.request_id)
        session.commit()
        return

    for key, value in outputs.items():
        existing = session.scalars(
            select(Output).where(Output.step_id == step.id, Output.key == key)
        ).one_or_none()
        if existing is None:
            session.add(Output(id=str(uuid.uuid4()), step_id=step.id, key=key, value=value))
        else:
            existing.value = value

    if step.status == "applying" and _produced_outputs_present(session, step):
        _transition_to_applied(session, step)
    session.commit()


def _flag_stuck_steps(session: Session, stuck_threshold_seconds: float | None) -> None:
    """Flag any Step held at `applying` past the threshold (architecture.md §14).

    A merged Step with apply-derived Outputs waits at `applying` for its Action
    to write them (ADR-0002); if the Action never does, the Step is stuck
    forever and nothing tells the human. Set `stuck` (queryable via the API,
    alertable via the log) the first tick a Step crosses the threshold, and log
    a single WARNING then — the `Step.stuck == False` guard is what keeps the
    ~15s driver from re-logging it every pass. `_transition` clears the flag if
    the outputs later arrive, so it never goes stale.
    """
    threshold = (
        stuck_threshold_seconds
        if stuck_threshold_seconds is not None
        else get_settings().step_stuck_threshold_seconds
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold)
    candidates = session.scalars(
        select(Step).where(
            Step.status == "applying",
            Step.stuck.is_(False),
            Step.status_changed_at < cutoff,
        )
    ).all()
    # A Step left at `applying` under a halted request (an operator cancel, or
    # a sibling Step's failure — #21) isn't "stuck holding for a human": the
    # request already stopped and the operator knows, so flagging it would be
    # noise. Only Steps of a still-live request are surfaced.
    newly_stuck = [s for s in candidates if s.request.status not in TERMINAL_REQUEST_STATUSES]
    for step in newly_stuck:
        step.stuck = True
        held_for = datetime.now(timezone.utc) - step.status_changed_at
        logger.warning(
            "step_stuck request_id=%s step=%s ordinal=%s status=applying held_for=%.0fs "
            "(no ADR-0002 output write; holding for a human)",
            step.request_id,
            step.key,
            step.ordinal,
            held_for.total_seconds(),
        )
    if newly_stuck:
        session.commit()


def _roll_up_request(session: Session, request_id: str) -> None:
    request = session.get(ProvisioningRequest, request_id)
    if request.status == "cancelled":
        # An operator cancellation (#21) isn't derivable from Step statuses
        # the way the other rollup outcomes below are, so it's not
        # recomputed away.
        return
    previous = request.status
    steps = request.steps
    if any(s.status in _FAILED_STEP_STATUSES for s in steps):
        request.status = "failed"
    elif all(s.status == "applied" for s in steps):
        request.status = "succeeded"
    elif any(s.status == "awaiting_approval" for s in steps):
        request.status = "awaiting_approval"
    else:
        request.status = "in_progress"

    if request.status != previous:
        logger.info(
            "request_rollup request_id=%s from=%s to=%s",
            request_id,
            previous,
            request.status,
        )
