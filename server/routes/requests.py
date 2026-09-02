"""`POST /v1/requests`, `GET /v1/requests/{id}`,
`GET /v1/requests/{id}/steps/{n}/plan`, and `POST /v1/requests/{id}/cancel`
(architecture.md §5, §6, §11).

`POST` checks the intake gate (`server.intake_gate`, #21) is open, validates
the body against its type's discriminated-union member (published in
`/openapi.json`), dedupes on `Idempotency-Key`, persists the
ProvisioningRequest, and expands its type's Recipe into a persisted Playbook
of one or more Steps — translating each StepSpec's `depends_on` (Step *keys*)
into the Step *row ids* generated here, since those ids don't exist until
insert time. The reconcile loop (`server.orchestrator`) then claims and
advances that Playbook's Steps; the `/plan` route just reads back the
`terraform plan` the loop captured once a Step has one, and `/cancel` halts
a request the same way a failed Step does.
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
        steps=[
            StepOut(
                ordinal=s.ordinal,
                key=s.key,
                status=s.status,
                pr_number=s.pr_number,
                pr_url=s.pr_url,
                plan_ref=s.plan_ref,
                depends_on=s.depends_on,
            )
            for s in request_row.steps
        ],
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
    # §10). A placeholder until real M2M auth context is wired up (fog).
    requester: str = Header(alias="X-Requester"),
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


class StepPlanResponse(BaseModel):
    ordinal: int
    key: str
    status: str
    plan: str


@router.get("/v1/requests/{request_id}/steps/{ordinal}/plan", response_model=StepPlanResponse)
def get_step_plan(
    request_id: str, ordinal: int, session: Session = Depends(get_db)
) -> StepPlanResponse:
    step = session.scalars(
        select(Step).where(Step.request_id == request_id, Step.ordinal == ordinal)
    ).first()
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.plan_ref is None:
        raise HTTPException(status_code=409, detail="Plan not available yet")
    return StepPlanResponse(ordinal=step.ordinal, key=step.key, status=step.status, plan=step.plan_ref)


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
