"""`POST /v1/requests`, `GET /v1/requests/{id}`, and
`GET /v1/requests/{id}/steps/{n}/plan` (architecture.md §5, §11).

`POST` validates the body against the type's Pydantic model (published in
`/openapi.json`), dedupes on `Idempotency-Key`, persists the
ProvisioningRequest, and expands its type's Recipe into a persisted
single-Step Playbook. The reconcile loop (`server.orchestrator`) then claims
and advances that Playbook's Steps; the `/plan` route just reads back the
`terraform plan` the loop captured once a Step has one.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.database import get_db
from server.models import ProvisioningRequest, Step
from server.recipes.registry import RECIPES
from server.recipes.schema import SchemaProvisioningRequest

router = APIRouter()

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
    body: SchemaProvisioningRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    # The self-service caller's identity, recorded for audit (architecture.md
    # §10). A placeholder until real M2M auth context is wired up (fog).
    requester: str = Header(alias="X-Requester"),
    session: Session = Depends(get_db),
) -> CreateRequestResponse:
    existing = _find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return CreateRequestResponse(request_id=existing.id, status=existing.status)

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

    for ordinal, step_spec in enumerate(playbook.steps):
        session.add(
            Step(
                id=str(uuid.uuid4()),
                request_id=request_row.id,
                ordinal=ordinal,
                key=step_spec.key,
                status="queued",
                depends_on=list(step_spec.depends_on),
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
