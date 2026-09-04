"""The reconcile loop (architecture.md §4.2, §5, §6; ADR-0004).

`tick()` is the orchestrator's synchronous entry point — the same one a timer
would call in production and a test calls directly for deterministic control
(architecture.md §14, "operator wants a synchronous advance/tick entry
point").

Per **ADR-0004** the API opens PRs and then **never polls GitHub for status**.
Every Step transition after "PR opened" is driven by a CI *push* (the
`PUT .../steps/{n}/outputs` ingress, ADR-0003, delegating into
`record_apply_result` below). So `tick()`'s only job is to open PRs for
runnable queued Steps; it reads no GitHub status. The Step lifecycle collapses
to:

    queued -> submitted -> { done | failed | rejected }

- **queued** — dependencies not yet `done`, or intake gated; no PR yet.
- **submitted** — the API opened the Step's PR and is waiting for CI's terminal
  push (this replaces the old `pr_open` + `awaiting_approval` + `applying`
  states, and with them the plan/merge polling passes).
- **done / failed / rejected** — CI reported the terminal outcome. `done`
  carries the Step's apply-derived Outputs; `rejected` is a PR closed without
  merging (a human declined it) reported by an on-close CI job, or an operator
  cancel. A preflight failure before the PR ever opens also lands at `failed`.

`_flag_stuck_steps` repurposes #43's surfacing: a Step held at `submitted` past
the threshold means CI's terminal push never arrived, so it is flagged for a
human (the on-close/terminal push failing to fire is the one way a Step could
otherwise hang forever — ADR-0004 "Consequences").
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
from server.github_client import GitHubClient, correlate
from server.models import Output, ProvisioningRequest, Step
from server.recipes.framework import AddFile, EditFile, FileEdit, StepSpec
from server.recipes.registry import RECIPES

logger = logging.getLogger(__name__)

# The Step outcomes CI may report over the ingress (ADR-0004).
APPLY_OUTCOMES = ("done", "failed", "rejected")

_FAILED_STEP_STATUSES = {"failed", "rejected"}

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
    """Advance the engine by one reconciliation pass (ADR-0004).

    The loop's only remaining job is to open PRs for currently-runnable queued
    Steps — a Step becomes runnable when its dependencies are `done`. Everything
    after "PR opened" is a CI push, not a poll, so there are no status-advance
    passes here any more.

    `stuck_threshold_seconds` overrides the configured
    `STEP_STUCK_THRESHOLD_SECONDS` (tests pass it to drive stuck detection
    deterministically); `None` reads it from settings.
    """
    while _claim_and_open_next(session, github_client):
        pass
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
            if s.request.status not in TERMINAL_REQUEST_STATUSES and _dependencies_done(session, s)
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
        _transition(step, "failed")
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
    # The PR is open; from here the API waits for CI's terminal push (ADR-0004),
    # it does not poll. `submitted` is that single "PR open, awaiting CI's
    # outcome" state — no plan wait, no merge poll.
    _transition(step, "submitted")

    _roll_up_request(session, step.request_id)
    session.commit()
    return True


def _dependencies_done(session: Session, step: Step) -> bool:
    if not step.depends_on:
        return True
    done_ids = session.scalars(
        select(Step.id).where(Step.id.in_(step.depends_on), Step.status == "done")
    ).all()
    return set(done_ids) == set(step.depends_on)


def _resolve_consumes(session: Session, step: Step) -> list[tuple[dict, Any]]:
    """Resolve each of this Step's OutputRefs (§5.1's `${steps.<key>.outputs.<name>}`)
    from Lakebase, by reference — never copied ahead of time. Safe to call once
    the Step is runnable: `_dependencies_done` already guarantees the referenced
    Step is `done`, and a Step only reaches `done` once CI's push has persisted
    its Outputs (see `record_apply_result`), so they are present.
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


def record_apply_result(
    session: Session,
    step: Step,
    outcome: str,
    outputs: dict[str, Any],
    tf_console: str,
) -> None:
    """Persist CI's terminal push for a submitted Step (ADR-0003/ADR-0004 — the
    `PUT .../steps/{n}/outputs` ingress calls straight into this).

    `outcome` is one of `APPLY_OUTCOMES`:

    - **done** — the apply succeeded. Each of `outputs` is upserted into
      `Output`, keyed by `(step_id, key)`, by overwriting an existing row's
      `value` in place rather than re-`add`ing one (a plain insert would trip
      `uq_output_step_key` on a retried report). The Step then runs its
      postflight hooks and lands at `done` — or at `failed` if a postflight
      hook raises (via `_land_after_postflight`). Because the outputs are
      persisted and the Step reaches `done` in this one call, a dependent
      Step's later `_resolve_consumes` never sees a blank reference — the
      atomic push closes the gap the old `applying` hold (#20) bridged.
    - **failed** — the apply ran and failed; the Step moves to `failed`.
    - **rejected** — the PR was closed without merging, or an on-close CI job
      reported it; the Step moves to `rejected`.

    `tf_console` is always stored, whatever the outcome (it may be empty for a
    `rejected` PR that never applied).

    Every transition is guarded by `_can_advance` (the Step is still
    `submitted` *and* its request hasn't been halted — an operator cancel or a
    sibling Step's failure, #21), so:

    - a repeated report against an already-terminal Step still updates
      `tf_console`/outputs but does not re-transition (no re-run postflight, no
      re-logged transition) — a retried push is idempotent, not merely
      non-erroring; and
    - a push arriving after the request was cancelled/failed is recorded but
      never advances the Step, honoring the "halt-no-rollback, no further
      advance" guarantee (#21).
    """
    if outcome not in APPLY_OUTCOMES:
        raise ValueError(f"Unknown apply outcome: {outcome!r}")

    step.tf_console = tf_console

    if outcome == "done":
        for key, value in outputs.items():
            existing = session.scalars(
                select(Output).where(Output.step_id == step.id, Output.key == key)
            ).one_or_none()
            if existing is None:
                session.add(Output(id=str(uuid.uuid4()), step_id=step.id, key=key, value=value))
            else:
                existing.value = value
        if _can_advance(step):
            _land_after_postflight(session, step)
        session.commit()
        return

    # failed / rejected — a terminal outcome with nothing to run.
    if _can_advance(step):
        _transition(step, outcome)
        _roll_up_request(session, step.request_id)
    session.commit()


def _can_advance(step: Step) -> bool:
    """A submitted Step may advance on a push only while its request is still
    live — a cancelled/failed request stays halted (#21), and a Step that
    already reached a terminal status is not re-transitioned (idempotent retry).
    """
    return step.status == "submitted" and step.request.status not in TERMINAL_REQUEST_STATUSES


def _land_after_postflight(session: Session, step: Step) -> None:
    """Run a done Step's postflight hooks and land it at `done` — or, if a hook
    raises, at `failed`. Postflight runs exactly once, right after CI's `done`
    push has persisted the Step's apply-derived Outputs.
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
        _transition(step, "failed")
    else:
        _transition(step, "done")
    _roll_up_request(session, step.request_id)


def _flag_stuck_steps(session: Session, stuck_threshold_seconds: float | None) -> None:
    """Flag any Step held at `submitted` past the threshold (ADR-0004).

    A submitted Step is waiting for CI's terminal push (`done`/`failed`/
    `rejected`). If that push never fires — the on-close job errors, an apply
    hangs, CI is misconfigured — the Step would sit at `submitted` forever and
    nothing would tell the human. Set `stuck` (queryable via the API, alertable
    via the log) the first tick a Step crosses the threshold, and log a single
    WARNING then — the `Step.stuck == False` guard is what keeps the ~15s driver
    from re-logging it every pass. `_transition` clears the flag if the push
    later arrives, so it never goes stale.
    """
    threshold = (
        stuck_threshold_seconds
        if stuck_threshold_seconds is not None
        else get_settings().step_stuck_threshold_seconds
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold)
    candidates = session.scalars(
        select(Step).where(
            Step.status == "submitted",
            Step.stuck.is_(False),
            Step.status_changed_at < cutoff,
        )
    ).all()
    # A Step left at `submitted` under a halted request (an operator cancel, or
    # a sibling Step's failure — #21) isn't "stuck waiting for a push": the
    # request already stopped and the operator knows, so flagging it would be
    # noise. Only Steps of a still-live request are surfaced.
    newly_stuck = [s for s in candidates if s.request.status not in TERMINAL_REQUEST_STATUSES]
    for step in newly_stuck:
        step.stuck = True
        held_for = datetime.now(timezone.utc) - step.status_changed_at
        logger.warning(
            "step_stuck request_id=%s step=%s ordinal=%s status=submitted held_for=%.0fs "
            "(no terminal CI push received; awaiting a human)",
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
    elif all(s.status == "done" for s in steps):
        request.status = "succeeded"
    else:
        request.status = "in_progress"

    if request.status != previous:
        logger.info(
            "request_rollup request_id=%s from=%s to=%s",
            request_id,
            previous,
            request.status,
        )
