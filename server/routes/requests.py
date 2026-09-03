"""`POST /v1/requests`, `GET /v1/requests/{id}`,
`GET /v1/requests/{id}/steps/{n}`, `GET /v1/requests/{id}/steps/{n}/plan`,
`PUT /v1/requests/{id}/steps/{n}/outputs`, and `POST /v1/requests/{id}/cancel`
(architecture.md §5, §6, §11).

`POST` checks the intake gate (`server.intake_gate`, #21) is open, validates
the body against its type's discriminated-union member (published in
`/openapi.json`), dedupes on `Idempotency-Key`, persists the
ProvisioningRequest, and expands its type's Recipe into a persisted Playbook
of one or more Steps — translating each StepSpec's `depends_on` (Step *keys*)
into the Step *row ids* generated here, since those ids don't exist until
insert time. The reconcile loop (`server.orchestrator`) then claims and
advances that Playbook's Steps; the `/plan` route just reads back the
`terraform plan` the loop captured once a Step has one, `/outputs` is the
ADR-0003 ingress a Step's GitHub Action PUTs its apply result to (#55, gated
on the CI M2M principal, delegating straight into `orchestrator.
record_apply_result`, #54), and `/cancel` halts a request the same way a
failed Step does.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Union

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server import orchestrator
from server.auth import require_ci_principal, resolve_requester
from server.database import get_db
from server.intake_gate import get_gate
from server.models import ProvisioningRequest, Step
from server.orchestrator import TERMINAL_REQUEST_STATUSES
from server.recipes.registry import RECIPES
from server.recipes.schema import SchemaProvisioningRequest
from server.recipes.workspace import WorkspaceProvisioningRequest

logger = logging.getLogger(__name__)

router = APIRouter()

ProvisioningRequestBody = Annotated[
    Union[SchemaProvisioningRequest, WorkspaceProvisioningRequest],
    Field(discriminator="type"),
]

# Pins how this route interacts with the Terramate bundles (architecture.md
# §7); hardcoded until a second version exists to select between.
API_VERSION = "v1"


class CreateRequestResponse(BaseModel):
    request_id: str
    status: str


class StepOut(BaseModel):
    ordinal: int
    key: str
    status: str
    pr_number: int | None
    pr_url: str | None
    plan_ref: str | None
    depends_on: list[str]
    # Stuck-surfacing (#43): `stuck` is true while this Step has been held at
    # `applying` past the threshold (its Action never wrote the ADR-0002
    # outputs), so an operator sees it here without needing log access;
    # `status_changed_at` is when it entered its current status.
    stuck: bool
    status_changed_at: datetime


class RequestDetailResponse(BaseModel):
    id: str
    type: str
    params: dict
    version: str
    requester: str
    status: str
    created_at: datetime
    updated_at: datetime
    steps: list[StepOut]


def _step_to_out(step: Step) -> StepOut:
    return StepOut(
        ordinal=step.ordinal,
        key=step.key,
        status=step.status,
        pr_number=step.pr_number,
        pr_url=step.pr_url,
        plan_ref=step.plan_ref,
        depends_on=step.depends_on,
        stuck=step.stuck,
        status_changed_at=step.status_changed_at,
    )


def _to_response(request_row: ProvisioningRequest) -> RequestDetailResponse:
    return RequestDetailResponse(
        id=request_row.id,
        type=request_row.type,
        params=request_row.params,
        version=request_row.version,
        requester=request_row.requester,
        status=request_row.status,
        created_at=request_row.created_at,
        updated_at=request_row.updated_at,
        steps=[_step_to_out(s) for s in request_row.steps],
    )


def _find_by_idempotency_key(session: Session, idempotency_key: str) -> ProvisioningRequest | None:
    return session.scalars(
        select(ProvisioningRequest).where(ProvisioningRequest.idempotency_key == idempotency_key)
    ).first()


@router.post("/v1/requests", response_model=CreateRequestResponse, status_code=202)
def create_request(
    body: ProvisioningRequestBody,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    # The self-service caller's identity, recorded for audit (architecture.md
    # §10) — resolved from the trusted Databricks Apps forwarded identity,
    # never from a client-controlled header (#47).
    requester: str = Depends(resolve_requester),
    session: Session = Depends(get_db),
) -> CreateRequestResponse:
    existing = _find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        logger.info(
            "request_idempotent_replay request_id=%s type=%s status=%s",
            existing.id,
            existing.type,
            existing.status,
        )
        return CreateRequestResponse(request_id=existing.id, status=existing.status)

    if not get_gate(session).enabled:
        logger.warning("request_rejected reason=intake_gate_closed type=%s requester=%s", body.type, requester)
        raise HTTPException(status_code=503, detail="Intake is currently disabled")

    recipe = RECIPES[body.type]
    playbook = recipe.build(body.params)

    request_row = ProvisioningRequest(
        id=str(uuid.uuid4()),
        type=body.type,
        params=body.params.model_dump(mode="json"),
        version=API_VERSION,
        requester=requester,
        idempotency_key=idempotency_key,
        status="pending",
    )
    session.add(request_row)

    # depends_on is authored against Step *keys* (StepSpec.depends_on) but
    # persisted as Step *row ids* (server.orchestrator._dependencies_applied
    # queries by id) — so ids are minted for every Step up front, in a
    # separate pass, before any Step row referencing another is built.
    step_ids_by_key = {step_spec.key: str(uuid.uuid4()) for step_spec in playbook.steps}

    for ordinal, step_spec in enumerate(playbook.steps):
        session.add(
            Step(
                id=step_ids_by_key[step_spec.key],
                request_id=request_row.id,
                ordinal=ordinal,
                key=step_spec.key,
                status="queued",
                depends_on=[step_ids_by_key[key] for key in step_spec.depends_on],
                produces=list(step_spec.produces),
                consumes=[
                    {"step_key": ref.step_key, "output_name": ref.output_name}
                    for ref in step_spec.consumes
                ],
            )
        )

    try:
        session.commit()
    except IntegrityError:
        # A concurrent request raced us on the same Idempotency-Key.
        session.rollback()
        existing = _find_by_idempotency_key(session, idempotency_key)
        if existing is None:
            raise
        return CreateRequestResponse(request_id=existing.id, status=existing.status)

    logger.info(
        "request_created request_id=%s type=%s requester=%s steps=%s",
        request_row.id,
        request_row.type,
        requester,
        len(playbook.steps),
    )
    return CreateRequestResponse(request_id=request_row.id, status=request_row.status)


@router.get("/v1/requests/{request_id}", response_model=RequestDetailResponse)
def get_request(request_id: str, session: Session = Depends(get_db)) -> RequestDetailResponse:
    request_row = session.get(ProvisioningRequest, request_id)
    if request_row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return _to_response(request_row)


def _find_step(session: Session, request_id: str, ordinal: int) -> Step | None:
    return session.scalars(
        select(Step).where(Step.request_id == request_id, Step.ordinal == ordinal)
    ).first()


@router.get("/v1/requests/{request_id}/steps/{ordinal}", response_model=StepOut)
def get_step(request_id: str, ordinal: int, session: Session = Depends(get_db)) -> StepOut:
    step = _find_step(session, request_id, ordinal)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    return _step_to_out(step)


class StepPlanResponse(BaseModel):
    ordinal: int
    key: str
    status: str
    plan: str


@router.get("/v1/requests/{request_id}/steps/{ordinal}/plan", response_model=StepPlanResponse)
def get_step_plan(
    request_id: str, ordinal: int, session: Session = Depends(get_db)
) -> StepPlanResponse:
    step = _find_step(session, request_id, ordinal)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.plan_ref is None:
        raise HTTPException(status_code=409, detail="Plan not available yet")
    return StepPlanResponse(ordinal=step.ordinal, key=step.key, status=step.status, plan=step.plan_ref)


class OutputReportRequest(BaseModel):
    applied: bool
    outputs: dict = Field(default_factory=dict)
    tf_console: str


class OutputReportResponse(BaseModel):
    ordinal: int
    key: str
    status: str


# The Step states a report may land on (ADR-0003, #55): `applying` is the
# real case — a merged Step waiting on its Action's report — and
# `applied`/`apply_failed` are a retried report against a Step this same
# endpoint already resolved (record_apply_result makes an identical retry
# there a no-op, see server.orchestrator). Any other status — the Step never
# reached `applying` (`queued`/`pr_open`/`awaiting_approval`), or it resolved
# some other way (`rejected`/`plan_failed`) — means the report doesn't match
# reality, so it's rejected rather than blindly overwriting `tf_console`/
# outputs on a Step nothing is waiting on.
_REPORTABLE_STEP_STATUSES = {"applying", "applied", "apply_failed"}


@router.put("/v1/requests/{request_id}/steps/{ordinal}/outputs", response_model=OutputReportResponse)
def report_step_outputs(
    request_id: str,
    ordinal: int,
    body: OutputReportRequest,
    # The CI M2M service principal a Step's GitHub Action authenticates as
    # (#47, #55) — this ingress writes provisioning truth, so it is gated the
    # same way the admin off-switch is: a trusted, platform-stamped forwarded
    # identity, never a client-controlled header.
    ci_principal: str = Depends(require_ci_principal),
    session: Session = Depends(get_db),
) -> OutputReportResponse:
    """`PUT /v1/requests/{id}/steps/{n}/outputs` (ADR-0003) — the HTTP ingress
    a Step's GitHub Action calls with its apply result. Resolves the
    step-scoped path to a Step row and delegates to the persistence seam
    (`orchestrator.record_apply_result`, #54); this route does not
    re-implement the transition/upsert logic itself, only validation, Step
    resolution, and the wrong-state policy below.
    """
    step = _find_step(session, request_id, ordinal)
    if step is None:
        logger.warning(
            "apply_result_rejected reason=step_not_found request_id=%s ordinal=%s",
            request_id,
            ordinal,
        )
        raise HTTPException(status_code=404, detail="Step not found")

    if step.status not in _REPORTABLE_STEP_STATUSES:
        logger.warning(
            "apply_result_rejected reason=wrong_step_state request_id=%s step=%s ordinal=%s status=%s",
            request_id,
            step.key,
            ordinal,
            step.status,
        )
        raise HTTPException(
            status_code=409,
            detail=f"Step is not in a state that accepts an apply report: {step.status}",
        )

    # Never log `outputs`/`tf_console` — they're the Action's reported
    # Terraform values and console text, not ours to assume are secret-free.
    orchestrator.record_apply_result(
        session, step, applied=body.applied, outputs=body.outputs, tf_console=body.tf_console
    )
    logger.info(
        "apply_result_accepted request_id=%s step=%s ordinal=%s applied=%s status=%s",
        request_id,
        step.key,
        ordinal,
        body.applied,
        step.status,
    )
    return OutputReportResponse(ordinal=step.ordinal, key=step.key, status=step.status)


class CancelRequestResponse(BaseModel):
    request_id: str
    status: str


@router.post("/v1/requests/{request_id}/cancel", response_model=CancelRequestResponse)
def cancel_request(request_id: str, session: Session = Depends(get_db)) -> CancelRequestResponse:
    """Halt an in-flight request (architecture.md §6, §11).

    Already-applied Steps stay applied — this only stops the reconcile loop
    from claiming or advancing this request's Steps any further (enforced by
    `orchestrator.TERMINAL_REQUEST_STATUSES`); it does not touch GitHub or
    any Step row. Cancelling an already-cancelled request is a no-op success
    (idempotent); cancelling one that reached a different terminal state on
    its own is a conflict, since that outcome can't be undone by cancelling.
    """
    request_row = session.get(ProvisioningRequest, request_id)
    if request_row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if request_row.status == "cancelled":
        return CancelRequestResponse(request_id=request_row.id, status=request_row.status)
    if request_row.status in TERMINAL_REQUEST_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"Request already reached a terminal state: {request_row.status}"
        )
    previous = request_row.status
    request_row.status = "cancelled"
    session.commit()
    logger.info("request_cancelled request_id=%s from=%s", request_row.id, previous)
    return CancelRequestResponse(request_id=request_row.id, status=request_row.status)
